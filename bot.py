"""Телеграм-бот «Дневник тренировок». Запуск: python bot.py"""
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
from exercises import ABS_TRISET, BY_KEY, EXERCISES, GROUPS, TEMPLATES
from export import export_xlsx
from matcher import Matcher
from parser import ParseError, SetRow, parse_sets, parse_triset, split_name
from plates import fmt_kg, plates_text
import nutrition as nut

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
BTN_FOOD = "🍽 Питание"
BTN_WATER = "💧 +250 мл"

REMIND_HOURS = [10, 12, 14, 16, 18, 20]   # когда напоминать про воду, если пьёшь меньше плана

MENU = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_START), KeyboardButton(text=BTN_FINISH)],
              [KeyboardButton(text=BTN_REPORT), KeyboardButton(text=BTN_HISTORY)],
              [KeyboardButton(text=BTN_FOOD), KeyboardButton(text=BTN_WATER)],
              [KeyboardButton(text=BTN_UNDO), KeyboardButton(text=BTN_HELP)]],
    resize_keyboard=True,
    input_field_placeholder="жим лежа 4х10х60",
)

HELP = (
    "🏋️ ТРЕНИРОВКА — одной строкой:\n"
    "• жим лежа 4х10х60 — 4 подхода по 10 повторов, 60 кг\n"
    "• жим лежа 60 10 10 8 8 — вес 60, повторы в каждом подходе\n"
    "• подтягивания 3х10 — свой вес\n"
    "• подтягивания 3х8 +16 — с гирей 16 кг на поясе\n"
    "• пресс — пресс-трисет: 2 круга 30 + 110 + 105\n"
    "• пресс 2 30 110 100 — свои круги и повторы\n\n"
    "Опечатки не страшны: «жиму леж» = Жим лёжа.\n"
    "Если бот не уверен, он покажет варианты кнопками.\n\n"
    "🍽 ПИТАНИЕ — пиши как говоришь, через запятую:\n"
    "• тарелка супа, 2 средних яблока, банан, стакан молока, гречка 150-200 г\n"
    "• творог 200, 3 яйца, курица 150, хлеб 2 куска\n"
    "• профиль 85 180 м 50 — вес, рост, пол, возраст (можно добавить: набор / похудение)\n"
    "• вес 84,5 — записать вес тела\n\n"
    "💧 ВОДА: кнопка «+250 мл» или «вода 500», «вода 2 стакана», «вода 1,5 л».\n"
    "Напоминания: /water_off — выключить, /water_on — включить.\n\n"
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

async def on_text(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    low = text.lower().replace("ё", "е")
    if low.startswith("/"):
        await message.answer("Не знаю такую команду.\n\n" + HELP)
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
                             "\n\n💧 Напоминания о воде включены (/water_off — выключить).")
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
    await add_water_and_reply(message, message.from_user.id, 250)


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
    if db.get_profile(message.from_user.id):
        db.set_profile_field(message.from_user.id, "water_reminders", 0)
    await message.answer("🔕 Напоминания о воде выключены. Включить: /water_on")


@router.message(Command("water_on"))
async def water_on(message: Message):
    if not db.get_profile(message.from_user.id):
        await message.answer("Сначала заполни профиль: профиль 85 180 м 50")
        return
    db.set_profile_field(message.from_user.id, "water_reminders", 1)
    hours = ", ".join(f"{h}:00" for h in REMIND_HOURS)
    await message.answer(f"🔔 Напоминания о воде включены: {hours}, если пьёшь меньше плана.")


def water_reminder_text(user_id: int, now: datetime) -> str | None:
    """Текст напоминания или None, если пить пока достаточно."""
    p = db.get_profile(user_id)
    goal = nut.targets(p)["water"]
    drunk = db.water_day(user_id, now.date())
    first, last = REMIND_HOURS[0] - 2, REMIND_HOURS[-1] + 1        # «день воды» с 8:00 до 21:00
    plan = goal * min(1.0, (now.hour - first) / (last - first))
    if drunk >= plan - 100:
        return None
    return (f"💧 Время выпить воды!\nСегодня: {drunk:.0f} из {goal:.0f} мл {nut.bar(drunk, goal)}\n"
            f"К этому часу по плану ≈{plan:.0f} мл.")


async def water_reminders(bot: Bot):
    kb = InlineKeyboardBuilder()
    kb.button(text="+250 мл", callback_data="water:250")
    kb.button(text="+500 мл", callback_data="water:500")
    markup = kb.as_markup()
    while True:
        await asyncio.sleep(60)
        now = datetime.now()
        if now.hour not in REMIND_HOURS:
            continue
        stamp = f"{now:%Y-%m-%d} {now.hour}"
        for p in db.reminder_users():
            if p["last_reminder"] == stamp:
                continue
            db.set_profile_field(p["user_id"], "last_reminder", stamp)
            try:
                text = water_reminder_text(p["user_id"], now)
                if text:
                    await bot.send_message(p["user_id"], text, reply_markup=markup)
            except Exception:
                logging.exception("Не удалось отправить напоминание о воде")


# Свободный текст обрабатывается последним, после всех кнопок и команд.
router.message.register(on_text, F.text)


async def main():
    if not TOKEN or TOKEN.startswith("вставь"):
        raise SystemExit("Не найден токен. Открой файл .env и впиши BOT_TOKEN=... (см. README.md)")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bot = Bot(TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    logging.info("Бот запущен. Остановить: Ctrl+C")
    reminders = asyncio.create_task(water_reminders(bot))
    try:
        await dp.start_polling(bot)
    finally:
        reminders.cancel()


if __name__ == "__main__":
    asyncio.run(main())
