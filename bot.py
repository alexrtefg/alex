"""Телеграм-бот «Дневник тренировок». Запуск: python bot.py"""
import asyncio
import logging
import os
from datetime import date

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (BufferedInputFile, CallbackQuery, FSInputFile, KeyboardButton, Message,
                           ReplyKeyboardMarkup)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

import reports
from db import DB
from exercises import ABS_TRISET, BY_KEY, EXERCISES, GROUPS, TEMPLATES
from export import export_xlsx
from matcher import Matcher
from parser import ParseError, SetRow, parse_sets, parse_triset, split_name
from plates import fmt_kg, plates_text

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0").strip() or 0)
DB_PATH = os.getenv("DB_PATH", "gym.db")

db = DB(DB_PATH)
matcher = Matcher(db.learned_aliases())
router = Router()
if OWNER_ID:  # бот отвечает только владельцу
    router.message.filter(F.from_user.id == OWNER_ID)
    router.callback_query.filter(F.from_user.id == OWNER_ID)

# Временная память: что ждёт ответа на кнопку.
pending_pick: dict[int, dict] = {}   # выбор упражнения из вариантов
pending_text: dict[int, str] = {}    # запись, присланная до начала тренировки

BTN_START = "▶️ Начать тренировку"
BTN_FINISH = "⏹ Завершить"
BTN_REPORT = "📊 Отчёт"
BTN_HISTORY = "📅 История"
BTN_UNDO = "↩️ Отменить последнее"
BTN_HELP = "❓ Помощь"

MENU = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_START), KeyboardButton(text=BTN_FINISH)],
              [KeyboardButton(text=BTN_REPORT), KeyboardButton(text=BTN_HISTORY)],
              [KeyboardButton(text=BTN_UNDO), KeyboardButton(text=BTN_HELP)]],
    resize_keyboard=True,
    input_field_placeholder="жим лежа 4х10х60",
)

HELP = (
    "Как записывать (одной строкой):\n"
    "• жим лежа 4х10х60 — 4 подхода по 10 повторов, 60 кг\n"
    "• жим лежа 60 10 10 8 8 — вес 60, повторы в каждом подходе\n"
    "• подтягивания 3х10 — свой вес\n"
    "• подтягивания 3х8 +16 — с гирей 16 кг на поясе\n"
    "• пресс — пресс-трисет: 2 круга 30 + 110 + 105\n"
    "• пресс 2 30 110 100 — свои круги и повторы\n\n"
    "Опечатки не страшны: «жиму леж» = Жим лёжа.\n"
    "Если бот не уверен, он покажет варианты кнопками.\n\n"
    "Ещё:\n"
    "• блины 90 — что повесить на гриф (блины ez 40 — для EZ-грифа)\n"
    "• прогресс жим — история и рекорды упражнения\n"
    "• /exercises — список упражнений\n"
    "• /export — весь дневник в Excel\n"
    "• /backup — прислать файл базы (резервная копия)"
)


def today_str() -> str:
    return date.today().strftime("%d.%m.%Y")


def template_keys(wtype: str | None) -> list:
    return TEMPLATES[wtype]["exercises"] if wtype else []


def undo_kb(entry_ids: list[int]):
    kb = InlineKeyboardBuilder()
    kb.button(text="↩️ Отменить", callback_data="undo:" + ",".join(map(str, entry_ids)))
    return kb.as_markup()


def choose_type_kb(suggested: str):
    kb = InlineKeyboardBuilder()
    for t in ([suggested] + [x for x in TEMPLATES if x != suggested]):
        mark = "✅ " if t == suggested else ""
        kb.button(text=mark + TEMPLATES[t]["title"].replace("Тренировка ", ""), callback_data=f"wt:{t}")
    kb.adjust(1)
    return kb.as_markup()


# ---------- команды ----------

@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Я твой дневник тренировок 💪\n\n" + HELP +
        f"\n\nТвой Telegram ID: {message.from_user.id}",
        reply_markup=MENU)


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def cmd_help(message: Message):
    await message.answer(HELP, reply_markup=MENU)


@router.message(Command("exercises"))
async def cmd_exercises(message: Message):
    lines = []
    for g, title in GROUPS.items():
        names = [e.name for e in EXERCISES if e.group == g]
        lines.append(f"{title}:\n" + "\n".join(f"• {n}" for n in names))
    await message.answer("\n\n".join(lines))


@router.message(Command("export"))
async def cmd_export(message: Message):
    await send_export(message, message.from_user.id)


async def send_export(message: Message, user_id: int):
    data = export_xlsx(db, user_id)
    await message.answer_document(BufferedInputFile(data, filename=f"дневник_{date.today():%Y-%m-%d}.xlsx"))


@router.message(Command("backup"))
async def cmd_backup(message: Message):
    await message.answer_document(FSInputFile(DB_PATH, filename=f"gym_backup_{date.today():%Y-%m-%d}.db"),
                                  caption="Резервная копия базы. Сохрани файл в надёжное место.")


# ---------- тренировка ----------

@router.message(Command("new"))
@router.message(F.text == BTN_START)
async def start_workout(message: Message):
    await ask_workout_type(message, message.from_user.id)


async def ask_workout_type(message: Message, user_id: int):
    active = db.active_workout(user_id)
    if active and active["day"] == date.today().isoformat():
        await message.answer("Тренировка уже идёт, просто записывай упражнения.\n\n" +
                             reports.workout_report(db, active["id"]))
        return
    if active:  # забытая незавершённая тренировка прошлого дня
        if not db.delete_workout_if_empty(active["id"]):
            db.finish_workout(active["id"])
    last = db.last_workout(user_id)
    suggested = "B" if last and last["wtype"] == "A" else "A"
    text = f"📅 {today_str()}\nКакая сегодня тренировка?"
    if last:
        days = (date.today() - date.fromisoformat(last["day"])).days
        text += f"\nПрошлая: {reports.ddmmyyyy(last['day'])} ({'А' if last['wtype'] == 'A' else 'Б'}), прошло {days} дн."
    await message.answer(text, reply_markup=choose_type_kb(suggested))


@router.callback_query(F.data.startswith("wt:"))
async def on_workout_type(cb: CallbackQuery):
    wtype = cb.data.split(":")[1]
    user_id = cb.from_user.id
    active = db.active_workout(user_id)
    if active and active["day"] == date.today().isoformat():
        await cb.answer("Тренировка уже идёт")
        return
    prev_same = db.last_workout(user_id, wtype)
    wid = db.start_workout(user_id, wtype)
    text = f"📅 Тренировка {today_str()}\n{TEMPLATES[wtype]['title']}\n\nЗаписывай: жим лежа 4х10х60"
    if prev_same:
        body = reports.workout_report(db, prev_same["id"], title="Прошлый раз —")
        text += "\n\n" + body
    kb = InlineKeyboardBuilder()
    items = " + ".join(str(r) for _, r in ABS_TRISET["items"])
    kb.button(text=f"🔥 Пресс-трисет: {ABS_TRISET['rounds']} круга ({items})", callback_data="abs")
    await cb.message.edit_text(text, reply_markup=kb.as_markup())
    await cb.answer()
    if user_id in pending_text:
        await handle_entry(cb.message, user_id, pending_text.pop(user_id), wid, wtype)


@router.message(Command("finish"))
@router.message(F.text == BTN_FINISH)
async def finish_workout(message: Message):
    active = db.active_workout(message.from_user.id)
    if not active:
        await message.answer("Сейчас нет начатой тренировки.")
        return
    if db.delete_workout_if_empty(active["id"]):
        await message.answer("Тренировка была пустой, удалил её.")
        return
    db.finish_workout(active["id"])
    await message.answer("🏁 Тренировка завершена!\n\n" + reports.workout_report(db, active["id"]))


@router.message(F.text == BTN_UNDO)
async def undo_last(message: Message):
    active = db.active_workout(message.from_user.id)
    last = db.last_entry(active["id"]) if active else None
    if not last:
        await message.answer("Нечего отменять.")
        return
    db.delete_entry(last["id"])
    await message.answer(f"↩️ Удалил последнюю запись: {reports.ex_name(last['exercise_key'])}")


@router.callback_query(F.data.startswith("undo:"))
async def on_undo(cb: CallbackQuery):
    ids = [int(x) for x in cb.data.split(":")[1].split(",")]
    deleted = sum(db.delete_entry(i) for i in ids)
    await cb.message.edit_text(cb.message.text + ("\n\n↩️ Отменено" if deleted else "\n\n(уже удалено)"))
    await cb.answer()


@router.callback_query(F.data == "abs")
async def on_abs(cb: CallbackQuery):
    active = db.active_workout(cb.from_user.id)
    if not active:
        await cb.answer("Сначала начни тренировку", show_alert=True)
        return
    reps = [r for _, r in ABS_TRISET["items"]]
    await save_triset(cb.message, active["id"], ABS_TRISET["rounds"], reps)
    await cb.answer()


async def save_triset(message: Message, workout_id: int, rounds: int, reps: list[int]):
    ids, lines = [], []
    for (key, _), r in zip(ABS_TRISET["items"], reps):
        ids.append(db.add_entry(workout_id, key, [SetRow(r) for _ in range(rounds)], "трисет"))
        lines.append(f"• {reports.ex_name(key)} — {rounds}×{r}")
    await message.answer(f"✅ {ABS_TRISET['title']}, кругов: {rounds}\n" + "\n".join(lines),
                         reply_markup=undo_kb(ids))


# ---------- отчёты ----------

@router.message(Command("report"))
@router.message(F.text == BTN_REPORT)
async def report_menu(message: Message):
    kb = InlineKeyboardBuilder()
    for text, data in (("Тренировка", "rep:workout"), ("Неделя", "rep:week"), ("Месяц", "rep:month"),
                       ("Год", "rep:year"), ("📥 Excel", "rep:excel")):
        kb.button(text=text, callback_data=data)
    kb.adjust(3, 2)
    await message.answer("Какой отчёт? Для одного упражнения напиши: прогресс жим", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("rep:"))
async def on_report(cb: CallbackQuery):
    kind = cb.data.split(":")[1]
    user_id = cb.from_user.id
    await cb.answer()
    if kind == "excel":
        await send_export(cb.message, user_id)
        return
    if kind == "workout":
        w = db.active_workout(user_id) or db.last_workout(user_id)
        text = reports.workout_report(db, w["id"]) if w else "Тренировок пока нет."
    else:
        text = reports.period_report(db, user_id, kind)
    await cb.message.answer(text)


@router.message(Command("history"))
@router.message(F.text == BTN_HISTORY)
async def history(message: Message):
    ws = db.recent_workouts(message.from_user.id, 12)
    if not ws:
        await message.answer("Тренировок пока нет.")
        return
    kb = InlineKeyboardBuilder()
    for w in ws:
        kb.button(text=f"{reports.ddmmyyyy(w['day'])} · {'А' if w['wtype'] == 'A' else 'Б'}",
                  callback_data=f"hist:{w['id']}")
    kb.adjust(2)
    await message.answer("Последние тренировки:", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("hist:"))
async def on_history(cb: CallbackQuery):
    wid = int(cb.data.split(":")[1])
    w = db.get_workout(wid)
    await cb.answer()
    if not w or w["user_id"] != cb.from_user.id:
        return
    await cb.message.answer(reports.workout_report(db, wid))


# ---------- выбор упражнения из вариантов ----------

@router.callback_query(F.data.startswith("pick:"))
async def on_pick(cb: CallbackQuery):
    key = cb.data.split(":")[1]
    p = pending_pick.pop(cb.from_user.id, None)
    if not p:
        await cb.answer("Запись устарела, отправь её ещё раз")
        return
    if key == "none":
        await cb.message.edit_text("Хорошо, не записываю. Список упражнений: /exercises")
        await cb.answer()
        return
    if matcher.is_generic(p["name"]):
        await cb.message.edit_text(f"Понял: {reports.ex_name(key)}.")
    else:  # запоминаем, как ты называешь это упражнение (опечатку или своё название)
        db.save_alias(p["name"], key)
        matcher.learn(p["name"], key)
        await cb.message.edit_text(f"Понял: «{p['name']}» = {reports.ex_name(key)}. Запомнил.")
    await cb.answer()
    await save_entry(cb.message, cb.from_user.id, p["workout_id"], key, p["numbers"], p["raw"])


async def save_entry(message: Message, user_id: int, workout_id: int, key: str, numbers: str, raw: str):
    ex = BY_KEY[key]
    if not numbers:
        last = reports.last_result_text(db, user_id, key, workout_id)
        await message.answer(f"{ex.name}: {last or 'записей ещё нет'}\n"
                             f"Напиши подходы, например: {raw} 4х10х60")
        return
    try:
        rows = parse_sets(numbers, bodyweight=ex.bodyweight)
    except ParseError as e:
        await message.answer(f"⚠️ {e}")
        return
    entry_id = db.add_entry(workout_id, key, rows, raw)
    saved = [dict(reps=r.reps, weight=r.weight, extra=r.extra) for r in rows]
    t = reports.tonnage(saved)
    text = f"✅ {ex.name}: {reports.fmt_sets(saved)}"
    if t:
        text += f" (тоннаж {fmt_kg(t)} кг)"
    last = reports.last_result_text(db, user_id, key, workout_id)
    if last:
        text += f"\n↗️ {last}"
    await message.answer(text, reply_markup=undo_kb([entry_id]))


# ---------- свободный текст: записи, блины, прогресс ----------

@router.message(F.text)
async def on_text(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    low = text.lower().replace("ё", "е")
    if low.startswith("/"):
        await message.answer("Не знаю такую команду.\n\n" + HELP)
        return

    if low.startswith("блины"):
        rest = low.removeprefix("блины").strip().replace(",", ".")
        bar = "straight"
        tokens = rest.split()
        if tokens and tokens[0] in ("ez", "из", "z", "з", "изогнутый", "изогнутым"):
            bar, rest = "ez", " ".join(tokens[1:])
        try:
            await message.answer(plates_text(float(rest), bar))
        except ValueError:
            await message.answer("Напиши вес, например: блины 90 или блины ez 40")
        return

    for prefix in ("прогресс", "отчет", "рекорд"):
        if low.startswith(prefix):
            name = low.removeprefix(prefix).strip()
            res = matcher.match(name)
            if res.status == "ok":
                await message.answer(reports.exercise_report(db, user_id, res.key))
            elif res.status == "ask":
                await message.answer("Уточни упражнение: " + ", ".join(reports.ex_name(k) for k, _ in res.options))
            else:
                await message.answer("Не нашёл такое упражнение. Пример: прогресс жим лежа")
            return

    active = db.active_workout(user_id)
    if not active or active["day"] != date.today().isoformat():
        pending_text[user_id] = text
        await message.answer("Сначала выбери тренировку, и я сразу запишу это.")
        await ask_workout_type(message, user_id)
        return
    await handle_entry(message, user_id, text, active["id"], active["wtype"])


async def handle_entry(message: Message, user_id: int, text: str, workout_id: int, wtype: str):
    try:
        tri = parse_triset(text, ABS_TRISET["keywords"], ABS_TRISET["rounds"],
                           [r for _, r in ABS_TRISET["items"]])
    except ParseError as e:
        await message.answer(f"⚠️ {e}")
        return
    if tri:
        await save_triset(message, workout_id, *tri)
        return

    name, numbers = split_name(text)
    if not name:
        await message.answer("Начни с названия упражнения, например: жим лежа 4х10х60")
        return
    res = matcher.match(name, prefer=template_keys(wtype))
    if res.status == "ok":
        await save_entry(message, user_id, workout_id, res.key, numbers, name)
    elif res.status == "ask":
        pending_pick[user_id] = dict(name=name, numbers=numbers, raw=name, workout_id=workout_id)
        kb = InlineKeyboardBuilder()
        for key, _ in res.options:
            kb.button(text=reports.ex_name(key), callback_data=f"pick:{key}")
        kb.button(text="❌ Ничего из этого", callback_data="pick:none")
        kb.adjust(1)
        await message.answer(f"Ты имел в виду «{name}» — это…?", reply_markup=kb.as_markup())
    else:
        await message.answer(f"🤔 Не нашёл упражнение «{name}». Список: /exercises\n"
                             f"Можно добавить новое в файл exercises.py.")


async def main():
    if not TOKEN or TOKEN.startswith("вставь"):
        raise SystemExit("Не найден токен. Открой файл .env и впиши BOT_TOKEN=... (см. README.md)")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bot = Bot(TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    logging.info("Бот запущен. Остановить: Ctrl+C")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
