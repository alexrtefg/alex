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

import clock

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (BufferedInputFile, CallbackQuery, FSInputFile, KeyboardButton, Message,
                           ReplyKeyboardMarkup)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

import reports
from db import DB
from exercises import KETTLEBELLS
from exercises import ABS_EXERCISES, ABS_TRISET, BY_KEY, EXERCISES, GROUPS, REST_LIGHT, TEMPLATES, rest_for
from export import export_xlsx
from matcher import Matcher
from parser import ParseError, SetRow, parse_sets, parse_triset, split_name
from plates import fmt_kg, plates_text
import nutrition as nut
from timer import DEFAULT_SIGNAL, Timers, mmss, parse_duration
import summary
import measures
import planner
import json

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0").strip() or 0)
DB_PATH = os.getenv("DB_PATH", "gym.db")

db = DB(DB_PATH)
db.backfill_body({e.key: e.bw_factor for e in EXERCISES if e.bw_factor})   # свой вес в старых записях подтягиваний
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
pending_numbers: dict[int, str] = {}  # цифры, присланные до выбора упражнения: запишем после выбора


def get_signal(user_id: int):
    text = db.get_setting(user_id, "signal_text", DEFAULT_SIGNAL)
    kind, file_id = db.get_setting(user_id, "signal_kind"), db.get_setting(user_id, "signal_file")
    return text, ((kind, file_id) if file_id else None)


timers = Timers(get_signal)
pending_edit: dict[int, int] = {}     # правка старой записи: user_id -> entry_id (ждём новые цифры)
for _row in db.custom_foods():        # свои продукты — в справочник и поиск
    nut.register_custom(_row)
TRAIN_REMIND_HOUR = 12                # когда напоминать о тренировке, если по ритму пора

BTN_START = "▶️ Начать"
BTN_FINISH = "⏹ Завершить"
BTN_REPORT = "📊 Отчёт"
BTN_HISTORY = "📅 История"
BTN_UNDO = "↩️ Отменить"
BTN_HELP = "❓ Помощь"
BTN_FOOD = "🍽 Питание"
BTN_WATER = "💧 +200 мл"
BTN_REST = "⏱ Отдых"
BTN_EXERCISES = "🏋️ Упражнения"
BTN_DAY = "📋 Итог дня"
BTN_SLEEP = "😴 Сон"
BTN_PLAN = "🧠 План"

# Старые надписи кнопок (если на телефоне осталась прежняя клавиатура) — тоже работают.
OLD_BUTTONS = {"▶️ Начать тренировку": "▶️ Начать", "↩️ Отменить последнее": "↩️ Отменить", "💧 +250 мл": "💧 +200 мл"}


def btn(text: str):
    """Фильтр кнопки: новая надпись или её старый вариант."""
    return F.text.in_({text, *[old for old, new in OLD_BUTTONS.items() if new == text]})


REMIND_HOURS = [9, 11, 13, 15, 17, 19, 21]   # когда напоминать про воду, пока не выпита норма
GLASS = nut.GLASS_ML                         # стакан воды, мл

MENU = ReplyKeyboardMarkup(   # 4 ряда по 3 кнопки — всё видно на экране телефона без прокрутки
    keyboard=[[KeyboardButton(text=BTN_START), KeyboardButton(text=BTN_EXERCISES), KeyboardButton(text=BTN_FINISH)],
              [KeyboardButton(text=BTN_REST), KeyboardButton(text=BTN_WATER), KeyboardButton(text=BTN_FOOD)],
              [KeyboardButton(text=BTN_SLEEP), KeyboardButton(text=BTN_DAY), KeyboardButton(text=BTN_REPORT)],
              [KeyboardButton(text=BTN_HISTORY), KeyboardButton(text=BTN_UNDO), KeyboardButton(text=BTN_PLAN)]],
    resize_keyboard=True,
    input_field_placeholder="3 80 8 — подходы, вес, повторы",
)

HELP = (
    "🏋️ ТРЕНИРОВКА\n"
    "Проще всего: кнопка «🏋️ Упражнения» → выбери упражнение → вводи только цифры:\n"
    "• 1 75 10 — 1 подход, 75 кг, 10 раз → запустится таймер отдыха\n"
    "• 3 80 8 — сразу 3 подхода по 80 кг на 8 раз → таймера не будет, бот спросит, что делаем далее\n"
    "• подтягивания, брусья, гипер: 1 10 — 1 подход на 10 раз; 1 20 10 — с весом 20 кг\n\n"
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
    "💪 После записи — кнопки «😌 Легко / 💪 Норм / 🥵 Тяжело»: бот подскажет, когда прибавлять вес.\n"
    "🤕 Боль: напиши «болит плечо» — запишу и учту в итоге дня.\n"
    "✏️ Исправить старую запись: «📅 История» → тренировка → нажми на запись.\n"
    "🍲 Свой продукт: «добавь продукт хинкали 235 10 12 22 шт 80» (ккал, Б, Ж, У на 100 г), список — «мои продукты».\n"
    "🔔 Напоминание о тренировке в 12:00, если по ритму пора: /train_off, /train_on, /train_test.\n"
    "📏 Замеры: «талия 88 грудь 102 бицепс 38 бедро 58 шея 40» (раз в месяц напомню), «замеры» — история.\n\n"
    "😴 СОН: кнопка «😴 Сон» или «сон 7», «спал 7,5», «сон 23:30-7:00».\n\n"
    "📋 ИТОГ ДНЯ: тренировка + питание + вода + сон вместе — что в норме, чего не хватает, "
    "что в избытке и что сделать.\n\n"
    "💧 ВОДА: кнопка «+200 мл» (стакан) или «вода 500», «вода 2 стакана», «вода 1,5 л».\n"
    "Напоминания каждые 2 часа с 9 до 21, пока не выпита норма: /water_off — выключить, /water_on — включить, "
    "/water_test — проверить.\n\n"
    "🧠 ПЛАН: кнопка «🧠 План» → отметь группы мышц → бот соберёт тренировку из твоего инвентаря "
    "(подходы, повторы, вес от прошлого раза, техника). Там же — советы по сплитам.\n\n"
    "Ещё:\n"
    "• блины 90 — что повесить на гриф (блины ez 40 — для EZ-грифа)\n"
    "• гантели 10 — как собрать пару наборных гантелей по 10 кг\n"
    "• прогресс жим — история и рекорды упражнения\n"
    "• /exercises — список упражнений\n"
    "• /export — весь дневник в Excel\n"
    "• /backup — прислать файл базы (резервная копия)"
)


def today_str() -> str:
    return clock.today().strftime("%d.%m.%Y")


WT_SHORT = {"A": "А", "B": "Б", "C": "своя"}


def plan_items(user_id: int) -> list:
    """Сохранённый план «🧠 План»: [[key, sets, reps], ...]."""
    raw = db.get_setting(user_id, "plan_items")
    try:
        return json.loads(raw) if raw else []
    except ValueError:
        return []


def template_keys(wtype: str | None, user_id: int | None = None) -> list:
    if wtype == "C" and user_id:
        return [k for k, _, _ in plan_items(user_id) if k in BY_KEY]
    return TEMPLATES[wtype]["exercises"] if wtype else []


def undo_kb(entry_ids: list[int], rest_seconds: int | None = None, effort: bool = False):
    kb = InlineKeyboardBuilder()
    sizes = []
    if effort:  # как дался подход: нужно, чтобы понимать, пора ли прибавлять вес
        last = entry_ids[-1]
        kb.button(text="😌 Легко", callback_data=f"eff:{last}:1")
        kb.button(text="💪 Норм", callback_data=f"eff:{last}:2")
        kb.button(text="🥵 Тяжело", callback_data=f"eff:{last}:3")
        sizes.append(3)
    kb.button(text="↩️ Отменить", callback_data="undo:" + ",".join(map(str, entry_ids)))
    if rest_seconds:
        kb.button(text=f"⏱ Отдых {mmss(rest_seconds)}", callback_data=f"rest:{rest_seconds}")
    sizes.append(2 if rest_seconds else 1)
    kb.adjust(*sizes)
    return kb.as_markup()


def autorest(user_id: int) -> bool:
    return db.get_setting(user_id, "autorest", "1") == "1"


async def start_rest(message: Message, user_id: int, seconds: int, label: str):
    await timers.start(message.bot, user_id, message.chat.id, seconds,
                       f"После: {label} · отдых {mmss(seconds)}")


async def after_record(message: Message, user_id: int, text: str, entry_ids: list[int], seconds: int, label: str,
                       n_sets: int = 1, hint: str = "1 75 10", effort: bool = True):
    """Ответ на запись.

    1 подход  -> таймер отдыха (сам или по кнопке), упражнение остаётся выбранным: дальше просто цифры.
    2+ подхода -> упражнение уже сделано целиком: таймер не нужен, спрашиваем, что делаем далее.
    """
    if n_sets > 1:
        selected.pop(user_id, None)
        await message.answer(text, reply_markup=undo_kb(entry_ids, effort=effort))
        await ask_next_exercise(message, user_id, "➡️ Какое упражнение делаем далее?")
        return
    auto = autorest(user_id)
    await message.answer(text + f"\nСледующий подход — просто цифры, например {hint}",
                         reply_markup=undo_kb(entry_ids, None if auto else seconds, effort=effort))
    if auto:
        await start_rest(message, user_id, seconds, label)


def exercise_menu_kb(wtype: str | None, user_id: int | None = None):
    kb = InlineKeyboardBuilder()
    if wtype == "C" and user_id:   # тренировка по плану: упражнения плана по порядку, с подходами
        items = [it for it in plan_items(user_id) if it[0] in BY_KEY]
        for i, (k, sets, reps) in enumerate(items, 1):
            kb.button(text=f"{i}. {BY_KEY[k].name} · {sets}×{reps}", callback_data=f"sel:{k}")
        kb.button(text="📋 Все упражнения по группам", callback_data="selall")
        kb.adjust(1)
        return kb.as_markup()
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
    await message.answer(title, reply_markup=exercise_menu_kb(active["wtype"] if active else None, user_id))


def selected_text(user_id: int, key: str, workout_id: int | None) -> str:
    ex = BY_KEY[key]
    last = reports.last_result_text(db, user_id, key, workout_id or -1)
    example = ("1 10 (подходы, повторы) · с весом: 1 20 10 (подходы, отягощение, повторы)"
               if ex.bodyweight else "1 75 10 (подходы, вес, повторы)")
    active = db.active_workout(user_id)
    by_plan = ""
    if active and active["wtype"] == "C":
        it = next((it for it in plan_items(user_id) if it[0] == key), None)
        if it:
            by_plan = f"По плану: {it[1]}×{it[2]}\n"
    return (f"👉 {ex.name}\n{by_plan}{last or 'записей ещё нет'}\n\n"
            f"Вводи цифры: {example}\nОтдых после подхода: {mmss(rest_for(key))}")


def choose_type_kb(suggested: str, resume_id: int | None = None):
    kb = InlineKeyboardBuilder()
    if resume_id:
        kb.button(text="↩️ Продолжить сегодняшнюю тренировку", callback_data=f"wtr:{resume_id}")
    for t in ([suggested] + [x for x in ("A", "B") if x != suggested]):
        mark = "✅ " if t == suggested else ""
        kb.button(text=mark + TEMPLATES[t]["title"].replace("Тренировка ", ""), callback_data=f"wt:{t}")
    kb.button(text="🧠 Собрать тренировку под себя", callback_data="pg:open")
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
@router.message(btn(BTN_HELP))
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
    await message.answer_document(BufferedInputFile(data, filename=f"дневник_{clock.today():%Y-%m-%d}.xlsx"))


@router.message(Command("backup"))
async def cmd_backup(message: Message):
    await message.answer_document(FSInputFile(DB_PATH, filename=f"gym_backup_{clock.today():%Y-%m-%d}.db"),
                                  caption="Резервная копия базы. Сохрани файл в надёжное место.")


# ---------- тренировка ----------

@router.message(Command("new"))
@router.message(btn(BTN_START))
async def start_workout(message: Message):
    await ask_workout_type(message, message.from_user.id)


async def ask_workout_type(message: Message, user_id: int):
    active = db.active_workout(user_id)
    if active and active["day"] == clock.today().isoformat():
        await message.answer("Тренировка уже идёт, просто записывай упражнения.\n\n" +
                             reports.workout_report(db, active["id"]))
        return
    if active:  # забытая незавершённая тренировка прошлого дня
        if not db.delete_workout_if_empty(active["id"]):
            db.finish_workout(active["id"])
    last = db.last_workout(user_id)
    suggested = "B" if last and last["wtype"] == "A" else "A"
    text = f"📅 {today_str()}\nКакая сегодня тренировка?"
    resume = last if last and last["day"] == clock.today().isoformat() else None
    if last and not resume:
        days = (clock.today() - date.fromisoformat(last["day"])).days
        text += f"\nПрошлая: {reports.ddmmyyyy(last['day'])} ({WT_SHORT.get(last['wtype'], '')}), прошло {days} дн."
    if resume:
        text += "\nСегодняшняя тренировка уже завершена — можно продолжить её или начать новую."
    await message.answer(text, reply_markup=choose_type_kb(suggested, resume["id"] if resume else None))


@router.callback_query(F.data.startswith("prog:"))
async def on_progress(cb: CallbackQuery):
    await cb.answer()
    await cb.message.answer(reports.exercise_report(db, cb.from_user.id, cb.data.split(":")[1]))


@router.callback_query(F.data.startswith("wtr:"))
async def on_resume(cb: CallbackQuery):
    wid = int(cb.data.split(":")[1])
    w = db.get_workout(wid)
    await cb.answer()
    if not w or w["user_id"] != cb.from_user.id:
        return
    db.conn.execute("UPDATE workouts SET finished_at=NULL WHERE id=?", (wid,))
    db.conn.commit()
    await cb.message.answer("↩️ Продолжаем сегодняшнюю тренировку.\n\n" + reports.workout_report(db, wid))
    await ask_next_exercise(cb.message, cb.from_user.id, "🏋️ Какое упражнение?")


@router.callback_query(F.data.startswith("wt:"))
async def on_workout_type(cb: CallbackQuery):
    wtype = cb.data.split(":")[1]
    user_id = cb.from_user.id
    active = db.active_workout(user_id)
    if active and active["day"] == clock.today().isoformat():
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
        await ask_wellbeing(cb.message)
        await handle_entry(cb.message, user_id, pending_text.pop(user_id), wid, wtype)
    elif user_id in selected:
        await cb.message.answer(selected_text(user_id, selected[user_id], wid))
    else:
        await ask_wellbeing(cb.message)
        await ask_next_exercise(cb.message, user_id, "🏋️ С какого упражнения начинаем?")


@router.message(Command("finish"))
@router.message(btn(BTN_FINISH))
async def finish_workout(message: Message):
    active = db.active_workout(message.from_user.id)
    if not active:
        await message.answer("Сейчас нет начатой тренировки.")
        return
    if db.delete_workout_if_empty(active["id"]):
        await message.answer("Тренировка была пустой, удалил её.")
        return
    db.finish_workout(active["id"])
    await message.answer("🏁 Тренировка завершена!\n\n" + reports.workout_report(db, active["id"]), reply_markup=MENU)


@router.message(btn(BTN_UNDO))
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
                       ids, REST_LIGHT, ABS_TRISET["title"], n_sets=rounds, effort=False)


# ---------- отчёты ----------

@router.message(Command("report"))
@router.message(btn(BTN_REPORT))
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
@router.message(btn(BTN_HISTORY))
async def history(message: Message):
    ws = db.recent_workouts(message.from_user.id, 12)
    if not ws:
        await message.answer("Тренировок пока нет.")
        return
    kb = InlineKeyboardBuilder()
    for w in ws:
        kb.button(text=f"{reports.ddmmyyyy(w['day'])} · {WT_SHORT.get(w['wtype'], '')}",
                  callback_data=f"hist:{w['id']}")
    kb.adjust(2)
    await message.answer("Последние тренировки (нажми, чтобы посмотреть или исправить):", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("hist:"))
async def on_history(cb: CallbackQuery):
    wid = int(cb.data.split(":")[1])
    w = db.get_workout(wid)
    await cb.answer()
    if not w or w["user_id"] != cb.from_user.id:
        return
    await send_workout_editor(cb.message, wid)


def entry_label(e) -> str:
    rows = db.entry_sets(e["id"])
    return f"{reports.ex_name(e['exercise_key'])}: {reports.fmt_sets(rows) if rows else '—'}"


async def send_workout_editor(message: Message, wid: int):
    kb = InlineKeyboardBuilder()
    entries = db.workout_entries(wid)
    for i, e in enumerate(entries, 1):
        kb.button(text=f"✏️ {i}. {entry_label(e)}"[:60], callback_data=f"ent:{e['id']}")
    kb.button(text="🗑 Удалить всю тренировку", callback_data=f"wdel:{wid}")
    kb.adjust(1)
    hint = "\n\nНажми на запись, чтобы исправить или удалить её." if entries else ""
    await message.answer(reports.workout_report(db, wid) + hint, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("ent:"))
async def on_entry(cb: CallbackQuery):
    eid = int(cb.data.split(":")[1])
    e = db.get_entry(eid)
    await cb.answer()
    if not e or db.get_workout(e["workout_id"])["user_id"] != cb.from_user.id:
        await cb.message.answer("Запись уже удалена.")
        return
    pending_edit[cb.from_user.id] = eid
    ex = BY_KEY.get(e["exercise_key"])
    example = "3 0 10 или 1 20 10" if ex and ex.bodyweight else "3 80 8"
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Удалить запись", callback_data=f"entdel:{eid}")
    kb.button(text="🔄 Другое упражнение", callback_data=f"entex:{eid}")
    kb.button(text="⬅️ Отмена", callback_data="entcancel")
    kb.adjust(2, 1)
    await cb.message.answer(f"✏️ {entry_label(e)}\n\nНапиши новые цифры (подходы, вес, повторы), например {example} — "
                            "и я заменю запись.", reply_markup=kb.as_markup())


@router.callback_query(F.data == "entcancel")
async def on_entry_cancel(cb: CallbackQuery):
    pending_edit.pop(cb.from_user.id, None)
    await cb.answer("Отменено")
    try:
        await cb.message.edit_text("Правка отменена.")
    except Exception:
        pass


@router.callback_query(F.data.startswith("entdel:"))
async def on_entry_delete(cb: CallbackQuery):
    eid = int(cb.data.split(":")[1])
    e = db.get_entry(eid)
    pending_edit.pop(cb.from_user.id, None)
    await cb.answer()
    if not e or db.get_workout(e["workout_id"])["user_id"] != cb.from_user.id:
        return
    db.delete_entry(eid)
    await cb.message.answer(f"🗑 Удалено: {reports.ex_name(e['exercise_key'])}")
    await send_workout_editor(cb.message, e["workout_id"])


@router.callback_query(F.data.startswith("entex:"))
async def on_entry_exercise(cb: CallbackQuery):
    eid = int(cb.data.split(":")[1])
    kb = InlineKeyboardBuilder()
    for g, title in GROUPS.items():
        kb.button(text=title, callback_data=f"entg:{eid}:{g}")
    kb.adjust(3)
    await cb.answer()
    await cb.message.answer("На какое упражнение заменить? Выбери группу:", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("entg:"))
async def on_entry_group(cb: CallbackQuery):
    _, eid, g = cb.data.split(":")
    kb = InlineKeyboardBuilder()
    for e in EXERCISES:
        if e.group == g:
            kb.button(text=e.name, callback_data=f"entset:{eid}:{e.key}")
    kb.adjust(1)
    await cb.answer()
    await cb.message.answer(f"{GROUPS[g]}:", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("entset:"))
async def on_entry_set_exercise(cb: CallbackQuery):
    _, eid, key = cb.data.split(":")
    e = db.get_entry(int(eid))
    await cb.answer()
    if not e:
        return
    w = db.get_workout(e["workout_id"])
    ex = BY_KEY[key]
    bw = db.body_weight_on(cb.from_user.id, date.fromisoformat(w["day"])) if ex.bw_factor else None
    db.set_entry_exercise(int(eid), key, round(bw * ex.bw_factor, 1) if bw else 0.0)
    pending_edit.pop(cb.from_user.id, None)
    await cb.message.answer(f"✅ Заменил на: {ex.name}")
    await send_workout_editor(cb.message, e["workout_id"])


async def apply_edit(message: Message, user_id: int, eid: int, numbers: str):
    e = db.get_entry(eid)
    if not e:
        await message.answer("Запись уже удалена.")
        return
    key = e["exercise_key"]
    ex = BY_KEY[key]
    numbers, note = fix_order(user_id, key, numbers)
    try:
        rows = parse_sets(numbers, bodyweight=ex.bodyweight)
    except ParseError as err:
        await message.answer(f"⚠️ {err}")
        pending_edit[user_id] = eid
        return
    w = db.get_workout(e["workout_id"])
    bw = db.body_weight_on(user_id, date.fromisoformat(w["day"])) if ex.bw_factor else None
    db.replace_entry_sets(eid, rows, round(bw * ex.bw_factor, 1) if bw else 0.0)
    await message.answer(f"✅ Исправлено: {ex.name} — " + reports.human_sets(
        [dict(reps=r.reps, weight=r.weight, extra=r.extra) for r in rows]) + (f"\n{note}" if note else ""))
    await send_workout_editor(message, e["workout_id"])


@router.callback_query(F.data.startswith("wdel:"))
async def on_workout_delete(cb: CallbackQuery):
    wid = int(cb.data.split(":")[1])
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Да, удалить", callback_data=f"wdelok:{wid}")
    kb.button(text="Нет", callback_data="entcancel")
    await cb.answer()
    await cb.message.answer("Точно удалить всю тренировку со всеми записями?", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("wdelok:"))
async def on_workout_delete_ok(cb: CallbackQuery):
    wid = int(cb.data.split(":")[1])
    w = db.get_workout(wid)
    await cb.answer()
    if w and w["user_id"] == cb.from_user.id:
        db.delete_workout(wid)
        await cb.message.edit_text(f"🗑 Тренировка {reports.ddmmyyyy(w['day'])} удалена.")


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


KB_EXERCISES = {"curl_kb", "kb_press", "kb_row", "kb_sumo", "kb_swing", "goblet_squat", "calf_standing"}


def fix_order(user_id: int, key: str, numbers: str) -> tuple[str, str]:
    """«3 12 16» — подходы, вес, повторы. Если похоже, что вес и повторы перепутаны местами, меняем и говорим об этом.

    Признаки: у гири вес бывает только 16/24/32; или прошлый рабочий вес ближе к последнему числу.
    """
    m = re.fullmatch(r"(\d+) (\d+(?:\.\d+)?) (\d+(?:\.\d+)?)", numbers.strip())
    if not m or BY_KEY[key].bodyweight:
        return numbers, ""
    sets, w, r = m.group(1), float(m.group(2)), float(m.group(3))
    if not r.is_integer() and w.is_integer():
        swap = True                                     # «3 10 57.5»: дробное — это вес
    elif key in KB_EXERCISES:
        swap = r in KETTLEBELLS and w not in KETTLEBELLS
    else:
        hist = db.exercise_history(user_id, key)
        prev = hist[-1]["weight"] if hist else None
        swap = bool(prev) and w <= 30 and abs(r - prev) < abs(w - prev)
    if not swap:
        return numbers, ""
    return (f"{sets} {fmt_kg(r).replace(',', '.')} {int(w)}",
            f"ℹ️ Понял как {fmt_kg(r)} кг на {int(w)} раз. Формат: подходы, вес, повторы.")


async def save_entry(message: Message, user_id: int, workout_id: int, key: str, numbers: str, raw: str):
    ex = BY_KEY[key]
    selected[user_id] = key
    if not numbers:
        await message.answer(selected_text(user_id, key, workout_id))
        return
    numbers, swap_note = fix_order(user_id, key, numbers)
    try:
        rows = parse_sets(numbers, bodyweight=ex.bodyweight)
    except ParseError as e:
        await message.answer(f"⚠️ {e}")
        return
    bw = db.body_weight_on(user_id) if ex.bw_factor else None
    body = round(bw * ex.bw_factor, 1) if bw else 0.0          # подтягивания, брусья: свой вес идёт в тоннаж
    entry_id = db.add_entry(workout_id, key, rows, raw, body=body)
    saved = [dict(reps=r.reps, weight=r.weight, extra=r.extra, body=body) for r in rows]
    t = reports.tonnage(saved)
    text = f"✅ {ex.name}: {reports.human_sets(saved)}"
    if t:
        text += f" (тоннаж {fmt_kg(t)} кг)"
    if swap_note:
        text += f"\n{swap_note}"
    last = reports.last_result_text(db, user_id, key, workout_id)
    if last:
        text += f"\n↗️ {last}"
    r0 = rows[0]
    hint = (f"1 {r0.reps}" if not r0.extra else f"1 {fmt_kg(r0.extra)} {r0.reps}") if ex.bodyweight \
        else f"1 {fmt_kg(r0.weight)} {r0.reps}"
    await after_record(message, user_id, text, [entry_id], rest_for(key), ex.name, n_sets=len(rows), hint=hint)


# ---------- свободный текст: записи, блины, прогресс ----------

async def on_text(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    low = text.lower().replace("ё", "е")
    if low.startswith("/"):
        await message.answer("Не знаю такую команду.\n\n" + HELP)
        return

    eid = pending_edit.pop(user_id, None)
    if eid and re.fullmatch(r"[\d\s.,+хx*×]+", low) and re.search(r"\d", low):
        await apply_edit(message, user_id, eid, split_name(text)[1])
        return

    if re.match(r"(добавь|добавить|новый|создай)\s+(продукт|еду|блюдо)", low):
        try:
            data = nut.parse_custom_food(text)
        except ValueError as e:
            await message.answer(str(e))
            return
        fid = db.add_custom_food(user_id, **data)
        f = nut.register_custom(db.conn.execute("SELECT * FROM custom_foods WHERE id=?", (fid,)).fetchone())
        extra = f", 1 шт = {fmt_kg(data['piece'])} г" if data["piece"] else ""
        extra += f", порция = {fmt_kg(data['portion'])} г" if data["portion"] else ""
        await message.answer(f"✅ Добавил продукт «{f.name}»: на 100 г {fmt_kg(f.kcal)} ккал · Б {fmt_kg(f.protein)} · "
                             f"Ж {fmt_kg(f.fat)} · У {fmt_kg(f.carbs)}{extra}.\n"
                             f"Теперь пиши как обычно: {f.name} 2 шт · {f.name} 300 г\nСписок: мои продукты")
        return

    if low in ("мои продукты", "/myfoods"):
        rows = db.custom_foods(user_id)
        if not rows:
            await message.answer("Своих продуктов пока нет.\n" + nut.CUSTOM_HELP)
            return
        kb = InlineKeyboardBuilder()
        lines = ["🍲 Мои продукты (на 100 г):"]
        for r in rows:
            lines.append(f"• {r['name']}: {fmt_kg(r['kcal'])} ккал · Б {fmt_kg(r['protein'])} · Ж {fmt_kg(r['fat'])} · "
                         f"У {fmt_kg(r['carbs'])}" + (f" · шт {fmt_kg(r['piece'])} г" if r["piece"] else ""))
            kb.button(text=f"🗑 {r['name']}", callback_data=f"cfdel:{r['id']}")
        kb.adjust(2)
        await message.answer("\n".join(lines), reply_markup=kb.as_markup())
        return

    m = re.fullmatch(r"(болит|боль|побаливает|ноет|тянет)\s+(.+)", low)
    if m:
        await save_pain(message, user_id, m.group(2).strip()[:100])
        return

    if re.match(r"(замер|/measure)", low) or measures.looks_like_measures(low):
        await handle_measures(message, user_id, low)
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
        if not active or active["day"] != clock.today().isoformat():
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

    if low.startswith("профиль") or nut.looks_like_profile(text):
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
        if not 30 <= w <= 250:
            await message.answer("Проверь вес, например: вес 76,5")
            return
        prev = db.weight_history(user_id)
        db.log_weight(user_id, w)
        text_w = f"⚖️ Вес {fmt_kg(w)} кг записан."
        if prev:
            d = w - prev[-1]["weight"]
            text_w += (f" Прошлый раз ({reports.ddmmyyyy(prev[-1]['day'])}): {fmt_kg(prev[-1]['weight'])} кг "
                       f"({'+' if d >= 0 else '−'}{fmt_kg(abs(round(d, 1)))}).")
        p = db.get_profile(user_id)
        if p:
            t = nut.targets(p)
            text_w += f"\nНорма пересчитана: {t['kcal']:.0f} ккал, белок {t['protein']:.0f} г."
        else:
            text_w += "\nЧтобы бот считал норму питания, заполни профиль: 76 175 м 50 (вес, рост, пол, возраст)."
        await message.answer(text_w)
        return

    for prefix in ("прогресс", "отчет", "рекорд"):
        if low.startswith(prefix):
            name = low.removeprefix(prefix).strip()
            if not name:
                keys = [r["exercise_key"] for r in db.conn.execute(
                    "SELECT e.exercise_key, COUNT(*) c FROM entries e JOIN workouts w ON w.id=e.workout_id "
                    "WHERE w.user_id=? GROUP BY e.exercise_key ORDER BY c DESC LIMIT 20", (user_id,)).fetchall()
                        if r["exercise_key"] in BY_KEY]
                if not keys:
                    await message.answer("Пока нет записей упражнений.")
                    return
                kb = InlineKeyboardBuilder()
                for k in keys:
                    kb.button(text=BY_KEY[k].name, callback_data=f"prog:{k}")
                kb.adjust(2)
                await message.answer("📈 Прогресс какого упражнения показать?", reply_markup=kb.as_markup())
                return
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
        if nut.looks_like_food(text) or nut.looks_food_shaped(text):
            await handle_food(message, user_id, text)
            return
        if not ex_res or ex_res.status == "none" or not re.search(r"\d", text):
            await message.answer("🤔 Не понял. Это не похоже ни на упражнение, ни на еду.\n"
                                 "Примеры: жим лежа 4х10х60 · 2 яблока, тарелка супа · вода 500\n"
                                 "Все примеры: ❓ Помощь")
            return

    active = db.active_workout(user_id)
    if not active or active["day"] != clock.today().isoformat():
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
            pending_numbers[user_id] = numbers
            await ask_next_exercise(message, user_id, f"Какое это упражнение? Нажми — и я запишу «{numbers}»:")
            return
        await save_entry(message, user_id, workout_id, key, numbers, BY_KEY[key].name)
        return
    res = matcher.match(name, prefer=template_keys(wtype, user_id))
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
    rows = db.food_day(user_id, clock.today())
    water = db.water_day(user_id, clock.today())
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
                   "\nНапиши иначе или добавь свой продукт, например:\n"
                   f"добавь продукт {unknown[0].split()[0]} 235 10 12 22 шт 80\n"
                   "(ккал, белки, жиры, углеводы на 100 г — с упаковки или из интернета)")
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


@router.message(btn(BTN_WATER))
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
    total = db.water_day(user_id, clock.today())
    text = f"💧 +{ml} мл · сегодня {total:.0f} / {goal:.0f} мл {nut.bar(total, goal)}"
    if total >= goal:
        text += "\n✅ Норма воды на сегодня выполнена!"
    await message.answer(text, reply_markup=nut_undo_kb([], [wid]))


def food_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="Вчера", callback_data="nut:yesterday")
    kb.button(text="Неделя", callback_data="nut:week")
    kb.button(text="👤 Профиль и норма", callback_data="nut:profile")
    kb.button(text="🗑 Удалить продукт", callback_data="nut:del")
    kb.adjust(2, 2)
    return kb.as_markup()


def food_day_text(user_id: int, day: date, title: str) -> str:
    return nut.day_report(db.food_day(user_id, day), db.water_day(user_id, day), db.get_profile(user_id),
                          f"{title} {day:%d.%m.%Y}")


@router.message(Command("food"))
@router.message(btn(BTN_FOOD))
async def food_today(message: Message):
    await message.answer(food_day_text(message.from_user.id, clock.today(), "Питание за"),
                         reply_markup=food_menu_kb())


@router.callback_query(F.data.startswith("fdel:"))
async def on_food_delete(cb: CallbackQuery):
    user_id = cb.from_user.id
    what = cb.data.split(":")[1]
    await cb.answer()
    if what == "water":
        row = db.conn.execute("SELECT id, ml FROM water_log WHERE user_id=? AND day=? ORDER BY id DESC LIMIT 1",
                              (user_id, clock.today().isoformat())).fetchone()
        if row:
            db.delete_water(user_id, row["id"])
            await cb.message.answer(f"🗑 Убрал воду {row['ml']:.0f} мл.\n{day_summary(user_id)}")
        return
    if db.delete_food(user_id, int(what)):
        await cb.message.answer(f"🗑 Удалено.\n{day_summary(user_id)}")
    else:
        await cb.message.answer("Эта запись уже удалена.")


@router.callback_query(F.data.startswith("cfdel:"))
async def on_custom_food_delete(cb: CallbackQuery):
    fid = int(cb.data.split(":")[1])
    await cb.answer()
    if db.delete_custom_food(cb.from_user.id, fid):
        nut.unregister_custom(fid)
        await cb.message.answer("🗑 Продукт удалён из справочника (старые записи еды остались).")


@router.callback_query(F.data.startswith("nut:"))
async def on_nut(cb: CallbackQuery):
    kind = cb.data.split(":")[1]
    user_id = cb.from_user.id
    await cb.answer()
    if kind == "del":
        rows = db.food_day(user_id, clock.today())
        kb = InlineKeyboardBuilder()
        for r in rows:
            f = nut.FOOD_BY_KEY.get(r["food_key"])
            kb.button(text=f"🗑 {(f.name if f else r['food_key'])[:30]} · {r['grams']:.0f} г", callback_data=f"fdel:{r['id']}")
        if db.water_day(user_id, clock.today()):
            kb.button(text="🗑 Последнюю запись воды", callback_data="fdel:water")
        kb.adjust(1)
        await cb.message.answer("Что удалить из сегодняшнего питания?" if rows else "За сегодня еды не записано.",
                                reply_markup=kb.as_markup())
        return
    if kind == "yesterday":
        await cb.message.answer(food_day_text(user_id, clock.today() - timedelta(days=1), "Питание за"))
    elif kind == "profile":
        p = db.get_profile(user_id)
        await cb.message.answer(nut.profile_text(p) if p else "Профиль не заполнен. Пример: профиль 85 180 м 50")
    elif kind == "week":
        end = clock.today()
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


def rhythm_days(user_id: int) -> float:
    """Твой обычный интервал между тренировками (по последним 8), от 3 до 7 дней; по умолчанию 4."""
    days = sorted({date.fromisoformat(w["day"]) for w in db.recent_workouts(user_id, 9)})
    if len(days) < 3:
        return 4
    gaps = [(b - a).days for a, b in zip(days, days[1:])]
    return min(7, max(3, sum(gaps) / len(gaps)))


def train_reminder_text(user_id: int) -> str | None:
    """Напоминание, если по ритму пора тренироваться и сегодня тренировки ещё не было."""
    last = db.last_workout(user_id)
    if not last or last["day"] == clock.today().isoformat():
        return None
    days = (clock.today() - date.fromisoformat(last["day"])).days
    rhythm = rhythm_days(user_id)
    if days < round(rhythm):
        return None
    nxt = "Б — ноги" if last["wtype"] == "A" else "А — грудь, спина, руки"
    text = (f"🏋️ Прошло {days} дн. с прошлой тренировки ({reports.ddmmyyyy(last['day'])}, "
            f"{WT_SHORT.get(last['wtype'], '')}). Твой ритм — раз в {fmt_kg(round(rhythm, 1))} дн., значит сегодня тренировка!\n"
            f"Следующая по очереди: {nxt}. Или собери свою: «🧠 План».")
    if days >= rhythm + 3:
        text += "\n⚠️ Перерыв затянулся — начни с весами на ~10% ниже, чтобы втянуться без травм."
    sleep = db.get_sleep(user_id, clock.today())
    if sleep is not None and sleep < 6:
        text += f"\n😴 Спал {summary.fmt_hours(sleep)} — сегодня работай без рекордов."
    return text


def train_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="▶️ Начать тренировку", callback_data="go:start")
    kb.button(text="🧠 План", callback_data="pg:open")
    return kb.as_markup()


@router.callback_query(F.data == "go:start")
async def on_go_start(cb: CallbackQuery):
    await cb.answer()
    await ask_workout_type(cb.message, cb.from_user.id)


@router.message(Command("train_off"))
async def train_off(message: Message):
    db.set_setting(message.from_user.id, "train_rem", "0")
    await message.answer("🔕 Напоминания о тренировке выключены. Включить: /train_on")


@router.message(Command("train_on"))
async def train_on(message: Message):
    db.set_setting(message.from_user.id, "train_rem", "1")
    await message.answer(f"🔔 Напоминания о тренировке включены: в {TRAIN_REMIND_HOUR}:00, если по твоему ритму пора.\n"
                         "Проверить: /train_test")


@router.message(Command("train_test"))
async def train_test(message: Message):
    text = train_reminder_text(message.from_user.id)
    if not text:
        last = db.last_workout(message.from_user.id)
        text = ("Тренировок ещё нет — напоминать пока не о чем." if not last else
                f"Пока рано: по ритму следующая тренировка через "
                f"{max(0, round(rhythm_days(message.from_user.id)) - (clock.today() - date.fromisoformat(last['day'])).days)} дн.")
    await message.answer(text, reply_markup=train_kb())


async def water_reminders(bot: Bot):
    sent: set[str] = set()
    while True:
        await asyncio.sleep(30)
        now = datetime.now()
        if now.day == 1 and now.hour == 10:   # 1-го числа — напоминание о замерах
            for uid in db.reminder_users():
                stamp = f"m {uid} {now:%Y-%m}"
                if stamp not in sent:
                    sent.add(stamp)
                    try:
                        await bot.send_message(uid, "📏 Новый месяц — время замеров!\n" + measures.HOWTO)
                    except Exception:
                        logging.exception("Не удалось отправить напоминание о замерах")
        if now.hour == TRAIN_REMIND_HOUR:
            for uid in db.reminder_users():
                stamp = f"t {uid} {clock.today()}"
                if stamp in sent:
                    continue
                sent.add(stamp)
                try:
                    if db.get_setting(uid, "train_rem", "1") == "1":
                        text = train_reminder_text(uid)
                        if text:
                            await bot.send_message(uid, text, reply_markup=train_kb())
                except Exception:
                    logging.exception("Не удалось отправить напоминание о тренировке")
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
@router.message(btn(BTN_REST))
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
@router.message(btn(BTN_EXERCISES))
async def exercises_button(message: Message):
    user_id = message.from_user.id
    active = db.active_workout(user_id)
    if not active or active["day"] != clock.today().isoformat():
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
    if not active or active["day"] != clock.today().isoformat():
        await ask_workout_type(cb.message, user_id)
        return
    nums = pending_numbers.pop(user_id, None)
    if nums:
        await save_entry(cb.message, user_id, active["id"], key, nums, BY_KEY[key].name)
    else:
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


# ---------- тяжесть подхода, самочувствие, боль, замеры ----------

def effort_tip(key: str, effort: int, row) -> str:
    ex = BY_KEY[key]
    if effort == 3:
        return "Держи этот вес, пока он не станет «норм»."
    if effort == 2:
        return "Хорошо. Когда станет «легко» — прибавляй."
    # легко
    if ex.bodyweight:
        return "💡 Легко — в следующий раз +1–2 повтора или отягощение +2,5 кг."
    if key in ("curl_db", "lat_raise", "db_fly", "side_bends", "curl_kb", "calf_standing", "hammer", "db_row"):
        return "💡 Легко — у гантелей/гири вес шагами: сначала +1–2 повтора, потом бери тяжелее."
    return f"💡 Легко — в следующий раз можно {fmt_kg((row['weight'] or 0) + 2.5)} кг (+2,5 кг) или +1–2 повтора."


@router.callback_query(F.data.startswith("eff:"))
async def on_effort(cb: CallbackQuery):
    _, entry_id, effort = cb.data.split(":")
    entry_id, effort = int(entry_id), int(effort)
    e = db.get_entry(entry_id)
    if not e:
        await cb.answer("Запись уже удалена")
        return
    db.set_effort(entry_id, effort)
    rows = [r for r in db.workout_sets(e["workout_id"]) if r["entry_id"] == entry_id]
    tip = effort_tip(e["exercise_key"], effort, rows[0]) if rows else ""
    await cb.answer(reports.EFFORT[effort])
    try:
        await cb.message.edit_text(f"{cb.message.text}\nТяжесть: {reports.EFFORT[effort]}. {tip}",
                                   reply_markup=undo_kb([entry_id]))
    except Exception:
        pass


async def ask_wellbeing(message: Message):
    kb = InlineKeyboardBuilder()
    for v, t in ((1, "😫 1"), (2, "😕 2"), (3, "😐 3"), (4, "🙂 4"), (5, "😎 5")):
        kb.button(text=t, callback_data=f"wb:{v}")
    kb.button(text="🤕 Что-то болит", callback_data="wb:pain")
    kb.adjust(5, 1)
    await message.answer("Как самочувствие сегодня? (1 — плохо, 5 — отлично)", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("wb:"))
async def on_wellbeing(cb: CallbackQuery):
    v = cb.data.split(":")[1]
    await cb.answer()
    if v == "pain":
        await cb.message.answer("Напиши, что болит, например: болит плечо · болит поясница · болит колено")
        return
    active = db.active_workout(cb.from_user.id)
    if not active:
        return
    db.set_wellbeing(active["id"], int(v))
    text = f"Самочувствие: {v}/5 записано."
    if int(v) <= 2:
        text += "\n⚠️ Сегодня работай легче: веса на 10–20% ниже, без рекордов. Если плохо — лучше отдохнуть."
    try:
        await cb.message.edit_text(text)
    except Exception:
        await cb.message.answer(text)


async def save_pain(message: Message, user_id: int, what: str):
    active = db.active_workout(user_id)
    if active and active["day"] == clock.today().isoformat():
        db.add_pain(active["id"], what)
    else:
        prev = db.get_setting(user_id, f"pain:{clock.today().isoformat()}")
        db.set_setting(user_id, f"pain:{clock.today().isoformat()}", f"{prev}; {what}" if prev else what)
    await message.answer(f"🤕 Записал: болит {what}.\n"
                         "Не делай упражнения через боль: убери или замени то, что её вызывает, снизь вес. "
                         "Если боль острая или не проходит несколько дней — к врачу.")


async def handle_measures(message: Message, user_id: int, low: str):
    values = measures.parse_measures(low)
    if not values:
        await message.answer(measures.history_text(db, user_id))
        return
    before = {part: db.measure_history(user_id, part) for part in values}
    for part, v in values.items():
        db.add_measure(user_id, part, v)
    lines = ["📏 Замеры записаны:"]
    for part, v in values.items():
        line = f"• {measures.PARTS[part]}: {fmt_kg(v)} см"
        prev = [r for r in before[part] if r["day"] != clock.today().isoformat()]
        if prev:
            d = v - prev[-1]["value"]
            line += f" ({'+' if d >= 0 else '−'}{fmt_kg(abs(round(d, 1)))} с {reports.ddmmyyyy(prev[-1]['day'])})"
        lines.append(line)
    await message.answer("\n".join(lines))


# ---------- 🧠 План: тренировка из выбранных групп ----------

plan_state: dict[int, dict] = {}   # user_id -> {"groups": set, "seed": int, "plan": Plan}
PRESETS = {"full": ["chest", "back", "legs", "shoulders", "biceps", "triceps", "abs"],
           "upper": ["chest", "back", "shoulders", "biceps", "triceps", "abs"],
           "lower": ["legs", "abs"]}


def groups_kb(selected_groups: set):
    kb = InlineKeyboardBuilder()
    for g, title in GROUPS.items():
        kb.button(text=("✅ " if g in selected_groups else "") + title, callback_data=f"pg:t:{g}")
    kb.button(text="🧍 Всё тело", callback_data="pg:p:full")
    kb.button(text="⬆️ Верх", callback_data="pg:p:upper")
    kb.button(text="⬇️ Ноги + пресс", callback_data="pg:p:lower")
    kb.button(text="▶️ Собрать план", callback_data="pg:go")
    kb.button(text="📅 Сплиты", callback_data="pg:split")
    kb.button(text="❓ Помощь", callback_data="pg:help")
    kb.adjust(4, 3, 3, 1, 2)
    return kb.as_markup()


def plan_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Начать по плану", callback_data="pg:start")
    kb.button(text="🔄 Другой вариант", callback_data="pg:redo")
    kb.button(text="📖 Техника", callback_data="pg:tech")
    kb.button(text="⬅️ Другие группы", callback_data="pg:open")
    kb.adjust(1, 2, 1)
    return kb.as_markup()


@router.message(Command("plan"))
@router.message(btn(BTN_PLAN))
async def plan_button(message: Message):
    await open_group_picker(message, message.from_user.id)


async def open_group_picker(message: Message, user_id: int):
    st = plan_state.setdefault(user_id, {"groups": set(), "seed": 0, "plan": None})
    await message.answer("🧠 Что тренируем сегодня? Отметь группы мышц (можно несколько) и нажми «▶️ Собрать план».\n"
                         "Бот соберёт тренировку из твоего инвентаря.", reply_markup=groups_kb(st["groups"]))


async def send_plan(message: Message, user_id: int):
    st = plan_state[user_id]
    st["plan"] = planner.build_plan(db, user_id, list(st["groups"]), seed=st["seed"])
    await message.answer(planner.plan_text(st["plan"]), reply_markup=plan_kb())


@router.callback_query(F.data.startswith("pg:"))
async def on_plan(cb: CallbackQuery):
    user_id = cb.from_user.id
    parts = cb.data.split(":")
    action = parts[1]
    st = plan_state.setdefault(user_id, {"groups": set(), "seed": 0, "plan": None})
    if action == "open":
        await cb.answer()
        await open_group_picker(cb.message, user_id)
    elif action == "t":
        g = parts[2]
        st["groups"] ^= {g}
        await cb.answer()
        try:
            await cb.message.edit_reply_markup(reply_markup=groups_kb(st["groups"]))
        except Exception:
            pass
    elif action in ("p", "go", "redo"):
        if action == "p":
            st["groups"] = set(PRESETS[parts[2]])
        if action == "redo":
            st["seed"] += 1
        if not st["groups"]:
            await cb.answer("Отметь хотя бы одну группу", show_alert=True)
            return
        await cb.answer("Собираю…")
        await send_plan(cb.message, user_id)
    elif action == "tech":
        await cb.answer()
        if st.get("plan"):
            await cb.message.answer(planner.technique_text(st["plan"]))
    elif action == "split":
        await cb.answer()
        kb = InlineKeyboardBuilder()
        kb.button(text="🧍 Собрать фулбоди", callback_data="pg:p:full")
        await cb.message.answer(planner.splits_text(db, user_id), reply_markup=kb.as_markup())
    elif action == "help":
        await cb.answer()
        await cb.message.answer(HELP)
    elif action == "start":
        plan = st.get("plan")
        if not plan or not plan.items:
            await cb.answer("Сначала собери план", show_alert=True)
            return
        await cb.answer("Поехали! 💪")
        db.set_setting(user_id, "plan_items", json.dumps([[i.key, i.sets, i.reps] for i in plan.items],
                                                         ensure_ascii=False))
        active = db.active_workout(user_id)
        if active and active["day"] == clock.today().isoformat():
            db.conn.execute("UPDATE workouts SET wtype='C' WHERE id=?", (active["id"],))
            db.conn.commit()
        else:
            if active and not db.delete_workout_if_empty(active["id"]):
                db.finish_workout(active["id"])
            db.start_workout(user_id, "C")
        selected.pop(user_id, None)
        await cb.message.answer(f"📅 Тренировка {today_str()} — по плану ({len(plan.items)} упр., ≈{plan.minutes} мин)")
        await ask_wellbeing(cb.message)
        await ask_next_exercise(cb.message, user_id, "🏋️ Упражнения по плану — нажимай по порядку:")


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
@router.message(btn(BTN_SLEEP))
async def sleep_button(message: Message):
    kb = InlineKeyboardBuilder()
    for h in (5, 6, 7, 8, 9):
        kb.button(text=f"{h} ч", callback_data=f"sleep:{h}")
    for h in (5.5, 6.5, 7.5, 8.5):
        kb.button(text=f"{h:g} ч".replace(".", ","), callback_data=f"sleep:{h}")
    kb.adjust(5, 4)
    cur = db.get_sleep(message.from_user.id, clock.today())
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
@router.message(btn(BTN_DAY))
async def day_button(message: Message):
    await message.answer(summary.day_summary(db, message.from_user.id), reply_markup=day_kb())


@router.callback_query(F.data.startswith("sum:"))
async def on_summary(cb: CallbackQuery):
    await cb.answer()
    if cb.data == "sum:week":
        await cb.message.answer(summary.week_summary(db, cb.from_user.id))
    else:
        await cb.message.answer(summary.day_summary(db, cb.from_user.id, clock.today() - timedelta(days=1)))


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
