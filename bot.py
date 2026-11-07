"""Телеграм-бот «Дневник тренировок». Запуск: python bot.py"""
try:  # Касперский подменяет сертификаты HTTPS: берём доверенные сертификаты из Windows, а не только из Python
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import asyncio
import logging
import os
import re
from datetime import date, datetime, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (BufferedInputFile, CallbackQuery, FSInputFile, KeyboardButton, Message,
                           ReplyKeyboardMarkup)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

import reports
from db import DB
from exercises import ABS_EXERCISES, ABS_TRISET, BY_KEY, EXERCISES, GROUPS, REST_LIGHT, TEMPLATES, rest_for
from export import export_xlsx
from matcher import Matcher
from parser import ParseError, SetRow, parse_sets, parse_triset, split_name
from plates import fmt_kg, plates_text
import nutrition as nut
from timer import DEFAULT_SIGNAL, Timers, mmss, parse_duration
import summary

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
pending_voice: dict[int, tuple] = {}  # голосовое, которое можно сделать сигналом таймера
selected: dict[int, str] = {}         # упражнение, выбранное в меню: дальше можно вводить только цифры


def get_signal(user_id: int):
    text = db.get_setting(user_id, "signal_text", DEFAULT_SIGNAL)
    kind, file_id = db.get_setting(user_id, "signal_kind"), db.get_setting(user_id, "signal_file")
    return text, ((kind, file_id) if file_id else None)


timers = Timers(get_signal)

BTN_START = "▶️ Начать тренировку"
BTN_FINISH = "⏹ Завершить"
BTN_REPORT = "📊 Отчёт"
BTN_HISTORY = "📅 История"
BTN_UNDO = "↩️ Отменить последнее"
BTN_HELP = "❓ Помощь"
BTN_FOOD = "🍽 Питание"
BTN_WATER = "💧 +200 мл"
BTN_REST = "⏱ Отдых"
BTN_EXERCISES = "🏋️ Упражнения"
BTN_DAY = "📋 Итог дня"
BTN_SLEEP = "😴 Сон"

REMIND_HOURS = [9, 11, 13, 15, 17, 19, 21]   # когда напоминать про воду, пока не выпита норма
GLASS = nut.GLASS_ML                         # стакан воды, мл

MENU = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_START), KeyboardButton(text=BTN_FINISH)],
              [KeyboardButton(text=BTN_EXERCISES), KeyboardButton(text=BTN_REST)],
              [KeyboardButton(text=BTN_FOOD), KeyboardButton(text=BTN_WATER)],
              [KeyboardButton(text=BTN_SLEEP), KeyboardButton(text=BTN_DAY)],
              [KeyboardButton(text=BTN_REPORT), KeyboardButton(text=BTN_HISTORY)],
              [KeyboardButton(text=BTN_UNDO), KeyboardButton(text=BTN_HELP)]],
    resize_keyboard=True,
    input_field_placeholder="3 80 8 — подходы, вес, повторы",
)

HELP = (
    "🏋️ ТРЕНИРОВКА\n"
    "Проще всего: кнопка «🏋️ Упражнения» → выбери упражнение → вводи только цифры:\n"
    "• 1 75 10 — 1 подход, 75 кг, 10 раз → запустится таймер отдыха\n"
    "• 3 80 8 — сразу 3 подхода по 80 кг на 8 раз → таймера не будет, бот спросит, что делаем далее\n"
    "• гипер: 3 20 15 — с блином 20 кг; подтягивания: 3 0 10 — без веса\n\n"
    "Или одной строкой с названием:\n"
    "• жим лежа 3 80 8 · жим лежа 4х10х60\n"
    "• жим лежа 60 10 10 8 8 — вес 60, повторы в каждом подходе\n"
    "• подтягивания 3х10 — свой вес\n"
    "• подтягивания 3х8 +16 — с гирей 16 кг на поясе\n"
    "• пресс — пресс-трисет: 2 круга 30 + 110 + 105\n"
    "• пресс 2 30 110 100 — свои круги и повторы\n\n"
    "Опечатки не страшны: «жиму леж» = Жим лёжа.\n"
    "Если бот не уверен, он покажет варианты кнопками.\n\n"
    "⏱ ОТДЫХ: после записи подхода таймер запускается сам — присед и становая 3:00, базовые 2:00, "
    "лёгкие 1:30. Кнопка «⏱ Отдых» или «отдых 2:30» — запустить вручную.\n"
    "• /autorest_off — не запускать сам, /autorest_on — снова запускать\n"
    "• /signal Твой текст — своя фраза в конце отдыха\n"
    "• пришли боту голосовое — оно станет сигналом окончания отдыха (/signal_reset — убрать)\n\n"
    "🍽 ПИТАНИЕ — пиши как говоришь, через запятую:\n"
    "• тарелка супа, 2 средних яблока, банан, стакан молока, гречка 150-200 г\n"
    "• творог 200, 3 яйца, курица 150, хлеб 2 куска\n"
    "• профиль 85 180 м 50 — вес, рост, пол, возраст (можно добавить: набор / похудение)\n"
    "• вес 84,5 — записать вес тела\n\n"
    "😴 СОН: кнопка «😴 Сон» или «сон 7», «спал 7,5», «сон 23:30-7:00».\n\n"
    "📋 ИТОГ ДНЯ: тренировка + питание + вода + сон вместе — что в норме, чего не хватает, "
    "что в избытке и что сделать.\n\n"
    "💧 ВОДА: кнопка «+200 мл» (стакан) или «вода 500», «вода 2 стакана», «вода 1,5 л».\n"
    "Напоминания каждые 2 часа с 9 до 21, пока не выпита норма: /water_off — выключить, /water_on — включить, "
    "/water_test — проверить.\n\n"
    "Ещё:\n"
    "• блины 90 — что повесить на гриф (блины ez 40 — для EZ-грифа)\n"
    "• гантели 10 — как собрать пару наборных гантелей по 10 кг\n"
    "• прогресс жим — история и рекорды упражнения\n"
    "• /exercises — список упражнений\n"
    "• /export — весь дневник в Excel\n"
    "• /backup — прислать файл базы (резервная копия)"
)


def today_str() -> str:
    return date.today().strftime("%d.%m.%Y")


def template_keys(wtype: str | None) -> list:
    return TEMPLATES[wtype]["exercises"] if wtype else []


def undo_kb(entry_ids: list[int], rest_seconds: int | None = None):
    kb = InlineKeyboardBuilder()
    kb.button(text="↩️ Отменить", callback_data="undo:" + ",".join(map(str, entry_ids)))
    if rest_seconds:
        kb.button(text=f"⏱ Отдых {mmss(rest_seconds)}", callback_data=f"rest:{rest_seconds}")
    return kb.as_markup()


def autorest(user_id: int) -> bool:
    return db.get_setting(user_id, "autorest", "1") == "1"


async def start_rest(message: Message, user_id: int, seconds: int, label: str):
    await timers.start(message.bot, user_id, message.chat.id, seconds,
                       f"После: {label} · отдых {mmss(seconds)}")


async def after_record(message: Message, user_id: int, text: str, entry_ids: list[int], seconds: int, label: str,
                       n_sets: int = 1):
    """Ответ на запись.

    1 подход  -> таймер отдыха (сам или по кнопке), упражнение остаётся выбранным: дальше просто цифры.
    2+ подхода -> упражнение уже сделано целиком: таймер не нужен, спрашиваем, что делаем далее.
    """
    if n_sets > 1:
        selected.pop(user_id, None)
        await message.answer(text, reply_markup=undo_kb(entry_ids))
        await ask_next_exercise(message, user_id, "➡️ Какое упражнение делаем далее?")
        return
    auto = autorest(user_id)
    await message.answer(text + "\nСледующий подход — просто цифры, например 1 75 10",
                         reply_markup=undo_kb(entry_ids, None if auto else seconds))
    if auto:
        await start_rest(message, user_id, seconds, label)


def exercise_menu_kb(wtype: str | None):
    kb = InlineKeyboardBuilder()
    keys = [k for k in template_keys(wtype) if k not in ABS_EXERCISES] if wtype else []
    for k in keys:
        kb.button(text=BY_KEY[k].name, callback_data=f"sel:{k}")
    items = " + ".join(str(r) for _, r in ABS_TRISET["items"])
    kb.button(text=f"🔥 Пресс-трисет ({items})", callback_data="abs")
    kb.button(text="📋 Все упражнения по группам", callback_data="selall")
    kb.adjust(*([2] * (len(keys) // 2) + [1] * (len(keys) % 2) + [1, 1]))
    return kb.as_markup()


async def ask_next_exercise(message: Message, user_id: int, title: str):
    active = db.active_workout(user_id)
    await message.answer(title, reply_markup=exercise_menu_kb(active["wtype"] if active else None))


def selected_text(user_id: int, key: str, workout_id: int | None) -> str:
    ex = BY_KEY[key]
    last = reports.last_result_text(db, user_id, key, workout_id or -1)
    example = "3 20 15 (подходы, отягощение, повторы) или 3 0 15" if ex.bodyweight else "1 75 10 (подходы, вес, повторы)"
    return (f"👉 {ex.name}\n{last or 'записей ещё нет'}\n\n"
            f"Вводи цифры: {example}\nОтдых после подхода: {mmss(rest_for(key))}")


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
    db.set_setting(message.from_user.id, "known", "1")   # чтобы приходили напоминания о воде
    await message.answer(
        "Привет! Я твой дневник тренировок и питания 💪\n\n" + HELP +
        "\n\nНачни с профиля, чтобы я посчитал твою норму: профиль 85 180 м 50" +
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
    text = f"📅 Тренировка {today_str()}\n{TEMPLATES[wtype]['title']}"
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
    elif user_id in selected:
        await cb.message.answer(selected_text(user_id, selected[user_id], wid))
    else:
        await ask_next_exercise(cb.message, user_id, "🏋️ С какого упражнения начинаем?")


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
    await after_record(message, message_user(message), f"✅ {ABS_TRISET['title']}, кругов: {rounds}\n" + "\n".join(lines),
                       ids, REST_LIGHT, ABS_TRISET["title"], n_sets=rounds)


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
    selected[user_id] = key
    if not numbers:
        await message.answer(selected_text(user_id, key, workout_id))
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
    await after_record(message, user_id, text, [entry_id], rest_for(key), ex.name, n_sets=len(rows))


# ---------- свободный текст: записи, блины, прогресс ----------

async def on_text(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    low = text.lower().replace("ё", "е")
    if low.startswith("/"):
        await message.answer("Не знаю такую команду.\n\n" + HELP)
        return

    m = re.fullmatch(r"(сон|спал|спала|поспал)\s+(.+)", low)
    if m:
        hours = summary.parse_sleep(m.group(2))
        if hours is None:
            await message.answer("Напиши так: сон 7 · спал 7,5 · сон 23:30-7:00")
            return
        await save_sleep(message, user_id, hours)
        return

    if re.fullmatch(r"[\d\s.,+хx*×]+", low) and re.search(r"\d", low):   # только цифры: 1 75 10
        active = db.active_workout(user_id)
        if not active or active["day"] != date.today().isoformat():
            pending_text[user_id] = text
            await message.answer("Сначала выбери тренировку, и я сразу запишу это.")
            await ask_workout_type(message, user_id)
            return
        await handle_entry(message, user_id, text, active["id"], active["wtype"])
        return

    m = re.fullmatch(r"(отдых|таймер)\s*(.*)", low)
    if m:
        if not m.group(2):
            await rest_button(message)
            return
        seconds = parse_duration(m.group(2))
        if not seconds or not 10 <= seconds <= 900:
            await message.answer("Напиши время: отдых 90 · отдых 2:30 · отдых 3 (минуты)")
            return
        await start_rest(message, user_id, seconds, "подход")
        return

    m = re.fullmatch(r"(блины|гантел[ьяиюей]*)\s*(ez|из|z|з|изогнутый|изогнутым)?\s*(\d+(?:[.,]\d+)?)?\s*(кг)?", low)
    if m:
        bar = "dumbbell" if m.group(1).startswith("гантел") else ("ez" if m.group(2) else "straight")
        if not m.group(3):
            await message.answer("Напиши вес, например: блины 90 · блины ez 40 · гантели 10")
        else:
            await message.answer(plates_text(float(m.group(3).replace(",", ".")), bar))
        return

    if low.startswith("профиль"):
        try:
            p = nut.parse_profile(text)
        except ValueError as e:
            await message.answer(str(e))
            return
        db.save_profile(user_id, **p)
        await message.answer(nut.profile_text(db.get_profile(user_id)) +
                             "\n\n💧 Напоминания о воде: каждые 2 часа с 9 до 21, пока не выпита норма (/water_off — выключить).")
        return

    m = re.fullmatch(r"вес\s*(\d+(?:[.,]\d+)?)\s*(кг)?", low)
    if m:
        w = float(m.group(1).replace(",", "."))
        if not db.get_profile(user_id):
            await message.answer("Сначала заполни профиль: профиль 85 180 м 50")
            return
        prev = db.weight_history(user_id)
        db.log_weight(user_id, w)
        text_w = f"⚖️ Вес {fmt_kg(w)} кг записан."
        if prev:
            text_w += f" Прошлый раз ({reports.ddmmyyyy(prev[-1]['day'])}): {fmt_kg(prev[-1]['weight'])} кг."
        await message.answer(text_w + "\nНорма пересчитана: " +
                             f"{nut.targets(db.get_profile(user_id))['kcal']:.0f} ккал, "
                             f"белок {nut.targets(db.get_profile(user_id))['protein']:.0f} г.")
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

    is_triset = parse_triset_safe(text)
    name, _ = split_name(text)
    ex_res = matcher.match(name) if name else None
    if not is_triset and not (ex_res and ex_res.status == "ok"):
        if nut.looks_like_food(text):
            await handle_food(message, user_id, text)
            return
        if not ex_res or ex_res.status == "none" or not re.search(r"\d", text):
            await message.answer("🤔 Не понял. Это не похоже ни на упражнение, ни на еду.\n"
                                 "Примеры: жим лежа 4х10х60 · 2 яблока, тарелка супа · вода 500\n"
                                 "Все примеры: ❓ Помощь")
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
        key = selected.get(user_id)
        if not key:
            await ask_next_exercise(message, user_id, "Сначала выбери упражнение, потом вводи цифры (например 1 75 10):")
            return
        await save_entry(message, user_id, workout_id, key, numbers, BY_KEY[key].name)
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


def parse_triset_safe(text: str) -> bool:
    try:
        return parse_triset(text, ABS_TRISET["keywords"], ABS_TRISET["rounds"],
                            [r for _, r in ABS_TRISET["items"]]) is not None
    except ParseError:
        return True   # похоже на трисет, но с ошибкой — handle_entry объяснит


# ---------- питание и вода ----------

def nut_undo_kb(food_ids: list[int], water_ids: list[int]):
    data = ",".join([f"f{i}" for i in food_ids] + [f"w{i}" for i in water_ids])
    kb = InlineKeyboardBuilder()
    kb.button(text="↩️ Отменить", callback_data="nundo:" + data)
    return kb.as_markup()


def day_summary(user_id: int) -> str:
    rows = db.food_day(user_id, date.today())
    water = db.water_day(user_id, date.today())
    kcal = sum(r["kcal"] for r in rows)
    protein = sum(r["protein"] for r in rows)
    p = db.get_profile(user_id)
    if p:
        t = nut.targets(p)
        return (f"За сегодня: {kcal:.0f} / {t['kcal']:.0f} ккал · белок {protein:.0f} / {t['protein']:.0f} г · "
                f"вода {water:.0f} / {t['water']:.0f} мл")
    return f"За сегодня: {kcal:.0f} ккал · белок {protein:.0f} г · вода {water:.0f} мл"


async def handle_food(message: Message, user_id: int, text: str):
    items = nut.parse_food_text(text)
    lines, unknown, food_ids, water_ids = [], [], [], []
    for it in items:
        if it.kind == "water":
            water_ids.append(db.add_water(user_id, it.grams))
            lines.append(f"💧 Вода +{it.grams:.0f} мл")
        elif it.status == "none":
            unknown.append(it.raw)
        else:
            n = nut.nutrients(it.key, it.grams)
            food_ids.append(db.add_food(user_id, it.key, it.grams, n, it.raw))
            line = "• " + nut.item_line(it.food.name, it.grams, n)
            if it.status == "guess":
                line += f"  (понял «{it.raw}» как {it.food.name.lower()})"
            lines.append(line)
    out = []
    if lines:
        out.append("✅ Записал:\n" + "\n".join(lines))
    if unknown:
        out.append("❓ Не нашёл: " + ", ".join(f"«{u}»" for u in unknown) +
                   "\nНапиши иначе или добавь продукт в файл foods.py.")
    out.append(day_summary(user_id))
    kb = nut_undo_kb(food_ids, water_ids) if (food_ids or water_ids) else None
    await message.answer("\n\n".join(out), reply_markup=kb)


@router.callback_query(F.data.startswith("nundo:"))
async def on_nut_undo(cb: CallbackQuery):
    n = 0
    for token in cb.data.split(":")[1].split(","):
        if token.startswith("f"):
            n += db.delete_food(cb.from_user.id, int(token[1:]))
        elif token.startswith("w"):
            n += db.delete_water(cb.from_user.id, int(token[1:]))
    await cb.message.edit_text(cb.message.text + ("\n\n↩️ Отменено" if n else "\n\n(уже удалено)"))
    await cb.answer()


@router.message(F.text == BTN_WATER)
async def water_button(message: Message):
    await add_water_and_reply(message, message.from_user.id, GLASS)


@router.callback_query(F.data.startswith("water:"))
async def on_water(cb: CallbackQuery):
    await add_water_and_reply(cb.message, cb.from_user.id, int(cb.data.split(":")[1]))
    await cb.answer("Записал 💧")


async def add_water_and_reply(message: Message, user_id: int, ml: int):
    wid = db.add_water(user_id, ml)
    p = db.get_profile(user_id)
    goal = nut.targets(p)["water"] if p else nut.WATER_GOAL_ML
    total = db.water_day(user_id, date.today())
    text = f"💧 +{ml} мл · сегодня {total:.0f} / {goal:.0f} мл {nut.bar(total, goal)}"
    if total >= goal:
        text += "\n✅ Норма воды на сегодня выполнена!"
    await message.answer(text, reply_markup=nut_undo_kb([], [wid]))


def food_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="Вчера", callback_data="nut:yesterday")
    kb.button(text="Неделя", callback_data="nut:week")
    kb.button(text="👤 Профиль и норма", callback_data="nut:profile")
    kb.adjust(2, 1)
    return kb.as_markup()


def food_day_text(user_id: int, day: date, title: str) -> str:
    return nut.day_report(db.food_day(user_id, day), db.water_day(user_id, day), db.get_profile(user_id),
                          f"{title} {day:%d.%m.%Y}")


@router.message(Command("food"))
@router.message(F.text == BTN_FOOD)
async def food_today(message: Message):
    await message.answer(food_day_text(message.from_user.id, date.today(), "Питание за"),
                         reply_markup=food_menu_kb())


@router.callback_query(F.data.startswith("nut:"))
async def on_nut(cb: CallbackQuery):
    kind = cb.data.split(":")[1]
    user_id = cb.from_user.id
    await cb.answer()
    if kind == "yesterday":
        await cb.message.answer(food_day_text(user_id, date.today() - timedelta(days=1), "Питание за"))
    elif kind == "profile":
        p = db.get_profile(user_id)
        await cb.message.answer(nut.profile_text(p) if p else "Профиль не заполнен. Пример: профиль 85 180 м 50")
    elif kind == "week":
        end = date.today()
        days = db.nutrition_days(user_id, end - timedelta(days=6), end)
        if not days:
            await cb.message.answer("За неделю записей о питании нет.")
            return
        p = db.get_profile(user_id)
        t = nut.targets(p) if p else None
        k = len(days)
        avg = {f: sum(d[f] for d in days) / k for f in ("kcal", "protein", "fat", "carbs", "water")}
        lines = [f"🍽 Питание за 7 дней (дней с записями: {k})", "", "В среднем за день:"]
        for f, title, unit in (("kcal", "Калории", "ккал"), ("protein", "Белки", "г"), ("fat", "Жиры", "г"),
                               ("carbs", "Углеводы", "г"), ("water", "Вода", "мл")):
            line = f"• {title}: {avg[f]:.0f} {unit}"
            if t:
                line += f" (норма {t[f]:.0f}, {avg[f] / t[f] * 100:.0f}%)"
            lines.append(line)
        if t:
            ok = sum(1 for d in days if d["water"] >= t["water"])
            lines.append(f"\n💧 Норма воды выполнена: {ok} из {k} дн.")
            low_p = sum(1 for d in days if d["protein"] < t["protein"] * 0.8)
            if low_p:
                lines.append(f"🥩 Белка мало (< 80% нормы): {low_p} из {k} дн.")
        await cb.message.answer("\n".join(lines))


@router.message(Command("water_off"))
async def water_off(message: Message):
    db.set_setting(message.from_user.id, "water_rem", "0")
    await message.answer("🔕 Напоминания о воде выключены. Включить: /water_on")


@router.message(Command("water_on"))
async def water_on(message: Message):
    db.set_setting(message.from_user.id, "water_rem", "1")
    hours = ", ".join(f"{h}:00" for h in REMIND_HOURS)
    await message.answer(f"🔔 Напоминания о воде включены: {hours} — пока не выпита дневная норма.\n"
                         "Проверить прямо сейчас: /water_test")


@router.message(Command("water_test"))
async def water_test(message: Message):
    text = water_reminder_text(message.from_user.id, datetime.now()) or "✅ Норма воды на сегодня уже выполнена!"
    await message.answer(text, reply_markup=water_kb())


def water_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text=f"+{GLASS} мл", callback_data=f"water:{GLASS}")
    kb.button(text=f"+{2 * GLASS} мл", callback_data=f"water:{2 * GLASS}")
    return kb.as_markup()


def water_reminder_text(user_id: int, now: datetime) -> str | None:
    """Текст напоминания или None, если дневная норма уже выпита."""
    p = db.get_profile(user_id)
    goal = nut.targets(p)["water"] if p else nut.WATER_GOAL_ML
    drunk = db.water_day(user_id, now.date())
    if drunk >= goal:
        return None
    left = goal - drunk
    hours_left = sum(1 for h in REMIND_HOURS if h > now.hour) + 1
    per = min(2 * GLASS, max(GLASS, round(left / hours_left / 50) * 50))   # за раз 1–2 стакана, не больше
    return (f"💧 Время выпить воды!\nСегодня: {drunk:.0f} из {goal:.0f} мл {nut.bar(drunk, goal)}\n"
            f"Осталось {left:.0f} мл (≈{left / GLASS:.0f} стак. по {GLASS} мл). Сейчас выпей ≈{per} мл.")


async def water_reminders(bot: Bot):
    sent: set[str] = set()
    while True:
        await asyncio.sleep(30)
        now = datetime.now()
        if now.hour not in REMIND_HOURS:
            continue
        for uid in db.reminder_users():
            stamp = f"{uid} {now:%Y-%m-%d} {now.hour}"
            if stamp in sent:
                continue
            sent.add(stamp)
            try:
                text = water_reminder_text(uid, now)
                if text:
                    await bot.send_message(uid, text, reply_markup=water_kb())
            except Exception:
                logging.exception("Не удалось отправить напоминание о воде")


# ---------- таймер отдыха ----------

def message_user(message: Message) -> int:
    """ID пользователя: у сообщений бота (из кнопок) from_user — сам бот, тогда берём чат."""
    return message.chat.id if message.from_user is None or message.from_user.is_bot else message.from_user.id


@router.message(Command("rest"))
@router.message(F.text == BTN_REST)
async def rest_button(message: Message):
    user_id = message.from_user.id
    active = db.active_workout(user_id)
    last = db.last_entry(active["id"]) if active else None
    key = last["exercise_key"] if last else None
    await start_rest(message, user_id, rest_for(key) if key else 120, reports.ex_name(key) if key else "подход")


@router.callback_query(F.data.startswith("rest:"))
async def on_rest(cb: CallbackQuery):
    seconds = int(cb.data.split(":")[1])
    await cb.answer()
    await start_rest(cb.message, cb.from_user.id, seconds, "подход")


@router.callback_query(F.data.startswith("rt:"))
async def on_timer_control(cb: CallbackQuery):
    action = cb.data.split(":")[1]
    if action == "stop":
        t = timers.cancel(cb.from_user.id)
        await cb.answer("Таймер остановлен" if t else "Таймер уже закончился")
        try:
            await cb.message.edit_text("⏹ Отдых остановлен")
        except Exception:
            pass
        return
    left = timers.adjust(cb.from_user.id, int(action))
    if left is None:
        await cb.answer("Таймер уже закончился")
        return
    await cb.answer(f"Осталось {mmss(left)}")
    try:
        t = timers.active[cb.from_user.id]
        await cb.message.edit_text(timers._text(t.label, left), reply_markup=cb.message.reply_markup)
    except Exception:
        pass


@router.message(Command("autorest_on"))
async def autorest_on(message: Message):
    db.set_setting(message.from_user.id, "autorest", "1")
    await message.answer("⏱ Таймер отдыха будет запускаться сам после каждой записи.")


@router.message(Command("autorest_off"))
async def autorest_off(message: Message):
    db.set_setting(message.from_user.id, "autorest", "0")
    await message.answer("⏱ Таймер сам не запускается. Под записью будет кнопка «⏱ Отдых».")


@router.message(Command("signal"))
async def set_signal(message: Message):
    text = (message.text or "").split(maxsplit=1)
    if len(text) < 2:
        cur, media = get_signal(message.from_user.id)
        await message.answer(f"Сейчас в конце отдыха: «{cur}»{' + голосовое' if media else ''}\n"
                             "Поменять: /signal Пошёл заниматься, герой!\n"
                             "Голосом: просто пришли боту голосовое сообщение.")
        return
    db.set_setting(message.from_user.id, "signal_text", text[1].strip()[:200])
    await message.answer(f"🔔 Готово, в конце отдыха будет: «{text[1].strip()[:200]}»")


@router.message(Command("signal_reset"))
async def reset_signal(message: Message):
    for k in ("signal_text", "signal_kind", "signal_file"):
        db.set_setting(message.from_user.id, k, None)
    await message.answer(f"🔔 Сигнал сброшен: «{DEFAULT_SIGNAL}», без голосового.")


@router.message(F.voice | F.audio)
async def on_voice(message: Message):
    if message.voice:
        pending_voice[message.from_user.id] = ("voice", message.voice.file_id)
    else:
        pending_voice[message.from_user.id] = ("audio", message.audio.file_id)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, это сигнал конца отдыха", callback_data="sig:yes")
    kb.button(text="Нет", callback_data="sig:no")
    kb.adjust(1)
    await message.answer("Сделать это голосовое сигналом окончания отдыха?", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("sig:"))
async def on_signal_choice(cb: CallbackQuery):
    media = pending_voice.pop(cb.from_user.id, None)
    await cb.answer()
    if cb.data == "sig:yes" and media:
        db.set_setting(cb.from_user.id, "signal_kind", media[0])
        db.set_setting(cb.from_user.id, "signal_file", media[1])
        await cb.message.edit_text("🔔 Готово! Это голосовое придёт в конце каждого отдыха. Убрать: /signal_reset")
    else:
        await cb.message.edit_text("Хорошо, не меняю сигнал.")


# ---------- меню упражнений ----------

@router.message(Command("ex"))
@router.message(F.text == BTN_EXERCISES)
async def exercises_button(message: Message):
    user_id = message.from_user.id
    active = db.active_workout(user_id)
    if not active or active["day"] != date.today().isoformat():
        await ask_workout_type(message, user_id)
        return
    await ask_next_exercise(message, user_id, "🏋️ Выбери упражнение:")


@router.callback_query(F.data.startswith("sel:"))
async def on_select(cb: CallbackQuery):
    key = cb.data.split(":")[1]
    user_id = cb.from_user.id
    await cb.answer(BY_KEY[key].name)
    selected[user_id] = key
    active = db.active_workout(user_id)
    if not active or active["day"] != date.today().isoformat():
        await ask_workout_type(cb.message, user_id)
        return
    await cb.message.answer(selected_text(user_id, key, active["id"]))


@router.callback_query(F.data == "selall")
async def on_select_all(cb: CallbackQuery):
    kb = InlineKeyboardBuilder()
    for g, title in GROUPS.items():
        kb.button(text=title, callback_data=f"selgrp:{g}")
    kb.adjust(3)
    await cb.answer()
    await cb.message.answer("Выбери группу мышц:", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("selgrp:"))
async def on_select_group(cb: CallbackQuery):
    g = cb.data.split(":")[1]
    kb = InlineKeyboardBuilder()
    for e in EXERCISES:
        if e.group == g:
            kb.button(text=e.name, callback_data=f"sel:{e.key}")
    kb.adjust(1)
    await cb.answer()
    await cb.message.answer(f"{GROUPS[g]}:", reply_markup=kb.as_markup())


# ---------- сон и итог дня ----------

async def save_sleep(message: Message, user_id: int, hours: float):
    db.set_sleep(user_id, hours)
    text = f"😴 Сон {summary.fmt_hours(hours)} записан (норма {summary.SLEEP_MIN:g}–{summary.SLEEP_MAX:g} ч)."
    if hours < 6:
        text += "\nМало. Сегодня без рекордов — работай в обычных весах и ляг пораньше."
    elif hours < summary.SLEEP_MIN:
        text += "\nЧуть меньше нормы."
    else:
        text += "\n✅ Отлично выспался!"
    await message.answer(text)


@router.message(Command("sleep"))
@router.message(F.text == BTN_SLEEP)
async def sleep_button(message: Message):
    kb = InlineKeyboardBuilder()
    for h in (5, 6, 7, 8, 9):
        kb.button(text=f"{h} ч", callback_data=f"sleep:{h}")
    for h in (5.5, 6.5, 7.5, 8.5):
        kb.button(text=f"{h:g} ч".replace(".", ","), callback_data=f"sleep:{h}")
    kb.adjust(5, 4)
    cur = db.get_sleep(message.from_user.id, date.today())
    head = f"Сейчас записано: {summary.fmt_hours(cur)}\n" if cur else ""
    await message.answer(head + "😴 Сколько спал этой ночью? Или напиши: сон 23:30-7:00",
                         reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("sleep:"))
async def on_sleep(cb: CallbackQuery):
    await cb.answer()
    await save_sleep(cb.message, cb.from_user.id, float(cb.data.split(":")[1]))


def day_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="Вчера", callback_data="sum:yesterday")
    kb.button(text="Неделя", callback_data="sum:week")
    return kb.as_markup()


@router.message(Command("day"))
@router.message(F.text == BTN_DAY)
async def day_button(message: Message):
    await message.answer(summary.day_summary(db, message.from_user.id), reply_markup=day_kb())


@router.callback_query(F.data.startswith("sum:"))
async def on_summary(cb: CallbackQuery):
    await cb.answer()
    if cb.data == "sum:week":
        await cb.message.answer(summary.week_summary(db, cb.from_user.id))
    else:
        await cb.message.answer(summary.day_summary(db, cb.from_user.id, date.today() - timedelta(days=1)))


# Чужие сообщения (если указан OWNER_ID): не молчим, а объясняем и пишем в журнал.
stranger_router = Router()


@stranger_router.message()
async def on_stranger(message: Message):
    uid = message.from_user.id if message.from_user else 0
    logging.warning("Сообщение от ID %s не принято: в .env стоит OWNER_ID=%s. "
                    "Если это ты, впиши OWNER_ID=%s и перезапусти бота.", uid, OWNER_ID, uid)
    await message.answer(f"⛔ Это личный бот.\nТвой Telegram ID: {uid}\n\n"
                         f"Если это твой бот: открой файл .env, впиши OWNER_ID={uid} и перезапусти start.bat.")


@stranger_router.callback_query()
async def on_stranger_cb(cb: CallbackQuery):
    await cb.answer("Это личный бот", show_alert=True)


# Свободный текст обрабатывается последним, после всех кнопок и команд.
router.message.register(on_text, F.text)


async def main():
    if not TOKEN or TOKEN.startswith("вставь"):
        raise SystemExit("Не найден токен. Открой файл .env и впиши BOT_TOKEN=... (см. README.md)")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bot = Bot(TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    dp.include_router(stranger_router)
    if OWNER_ID:
        logging.info("Бот отвечает только владельцу: OWNER_ID=%s", OWNER_ID)
    else:
        logging.info("OWNER_ID не указан: бот отвечает всем. Свой ID узнаешь по команде /start")
    logging.info("Бот запущен. Остановить: Ctrl+C")
    reminders = asyncio.create_task(water_reminders(bot))
    try:
        await dp.start_polling(bot)
    finally:
        reminders.cancel()


if __name__ == "__main__":
    asyncio.run(main())
