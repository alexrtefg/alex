"""Сон и «Итог дня / недели»: тренировка + питание + вода + сон, недобор, перебор и советы (правила, без ИИ)."""
import re
from datetime import date, datetime, timedelta

import nutrition as nut
import reports
from db import DB
from exercises import TEMPLATES
from foods import BY_KEY as FOOD_BY_KEY, PROTEIN_SUGGESTIONS
from plates import fmt_kg

SLEEP_MIN, SLEEP_MAX = 7.0, 9.0   # норма сна для взрослого, часов
TRAIN_EVERY = (4, 5)             # твой ритм: тренировка раз в 4–5 дней


def parse_sleep(text: str) -> float | None:
    """«7», «7,5», «7:30», «7 ч 30 мин», «23:30-7:00» -> часы."""
    t = text.lower().replace(",", ".").strip()
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})\s*[-–—]\s*(\d{1,2})(?:[:.](\d{2}))?", t)   # 23:30-7:00
    if m:
        start = int(m.group(1)) + int(m.group(2)) / 60
        end = int(m.group(3)) + int(m.group(4) or 0) / 60
        h = (end - start) % 24
    elif m := re.fullmatch(r"(\d{1,2})\s*[-–—]\s*(\d{1,2})", t):                     # 23-7
        h = (int(m.group(2)) - int(m.group(1))) % 24
    elif m := re.fullmatch(r"(\d{1,2}):(\d{2})", t):                                  # 7:30
        h = int(m.group(1)) + int(m.group(2)) / 60
    elif m := re.fullmatch(r"(\d{1,2}(?:\.\d+)?)\s*(ч|час\w*)?\s*(?:(\d{1,2})\s*(м|мин\w*))?", t):  # 7 ч 30 мин
        h = float(m.group(1)) + (int(m.group(3)) / 60 if m.group(3) else 0)
    else:
        return None
    return round(h, 2) if 0 < h <= 16 else None


def fmt_hours(h: float) -> str:
    hh, mm = int(h), round((h - int(h)) * 60)
    return f"{hh} ч" + (f" {mm} мин" if mm else "")


def _pct(v, goal):
    return v / goal * 100 if goal else 0


def _protein_tip(gap: float) -> str:
    tips = []
    for key in PROTEIN_SUGGESTIONS[:3]:
        f = FOOD_BY_KEY[key]
        g = f.portions.get("порция", 100)
        tips.append(f"{f.name.lower()} {g} г (+{f.protein * g / 100:.0f} г)")
    return ", ".join(tips)


def day_summary(db: DB, user_id: int, day: date | None = None, now: datetime | None = None) -> str:
    day = day or date.today()
    now = now or datetime.now()
    p = db.get_profile(user_id)
    t = nut.targets(p) if p else None
    goal = p["goal"] if p else "maintain"

    food = db.food_day(user_id, day)
    tot = {k: sum(r[k] for r in food) for k in ("kcal", "protein", "fat", "carbs")}
    water = db.water_day(user_id, day)
    water_goal = t["water"] if t else nut.WATER_GOAL_ML
    sleep = db.get_sleep(user_id, day)

    ws = [w for w in db.workouts_between(user_id, day, day)]
    trained = bool(ws) and any(db.workout_sets(w["id"]) for w in ws)

    ok, low, high, tips = [], [], [], []
    lines = [f"📋 Итог дня {day:%d.%m.%Y}", ""]

    # --- тренировка ---
    if trained:
        w = ws[-1]
        rows = db.workout_sets(w["id"])
        ton = reports.tonnage(rows)
        line = f"🏋️ Тренировка: {TEMPLATES[w['wtype']]['title'].split(' — ')[1]}, подходов {len(rows)}, тоннаж {fmt_kg(ton)} кг"
        prev = db.last_workout(user_id, w["wtype"], exclude_id=w["id"])
        if prev and prev["day"] < w["day"]:
            prev_ton = reports.tonnage(db.workout_sets(prev["id"]))
            if prev_ton:
                diff = (ton - prev_ton) / prev_ton * 100
                line += f" ({diff:+.0f}% к прошлой)"
                if diff >= 3:
                    ok.append("тренировка — объём вырос")
        lines.append(line)
        ok.append("тренировка сделана")
    else:
        last = db.last_workout(user_id)
        if last:
            days = (day - date.fromisoformat(last["day"])).days
            lines.append(f"🏋️ Тренировки не было. С прошлой прошло {days} дн.")
            if days >= TRAIN_EVERY[1] + 2:
                tips.append(f"🏋️ Прошло {days} дн. с прошлой тренировки — пора в зал (твой ритм {TRAIN_EVERY[0]}–{TRAIN_EVERY[1]} дн.). "
                            "После паузы начни с весом на ~10% ниже.")
            elif days >= TRAIN_EVERY[0]:
                tips.append(f"🏋️ По твоему ритму сегодня-завтра тренировка (прошло {days} дн.).")
        else:
            lines.append("🏋️ Тренировок пока нет.")

    # --- питание ---
    if t:
        lines.append(f"🍽 Калории {tot['kcal']:.0f}/{t['kcal']:.0f} ({_pct(tot['kcal'], t['kcal']):.0f}%) · "
                     f"Б {tot['protein']:.0f}/{t['protein']:.0f} · Ж {tot['fat']:.0f}/{t['fat']:.0f} · "
                     f"У {tot['carbs']:.0f}/{t['carbs']:.0f} г")
        if not food:
            low.append("питание не записано")
        else:
            k = _pct(tot["kcal"], t["kcal"])
            lo, hi = (70, 105) if goal == "loss" else (80, 115)
            if k < lo:
                low.append(f"калории −{t['kcal'] - tot['kcal']:.0f} ккал")
                tips.append("🍽 Калорий мало — добавь полноценный приём пищи: каша/картофель + мясо или рыба + овощи.")
            elif k > hi:
                high.append(f"калории +{tot['kcal'] - t['kcal']:.0f} ккал")
                tips.append("🍽 Перебор калорий — завтра меньше сладкого, мучного и жареного, особенно вечером.")
            else:
                ok.append("калории")
            pr = _pct(tot["protein"], t["protein"])
            if pr < 90:
                gap = t["protein"] - tot["protein"]
                low.append(f"белок −{gap:.0f} г")
                tips.append(f"🥩 Добери белок {gap:.0f} г: {_protein_tip(gap)}. В 50 лет белок особенно важен для мышц.")
            else:
                ok.append("белок")
            f = _pct(tot["fat"], t["fat"])
            if f > 130:
                high.append(f"жиры +{tot['fat'] - t['fat']:.0f} г")
                tips.append("🧈 Много жиров — меньше колбасы, сосисок, масла, жареного.")
            elif f < 60:
                low.append(f"жиры −{t['fat'] - tot['fat']:.0f} г")
            else:
                ok.append("жиры")
            c = _pct(tot["carbs"], t["carbs"])
            if c > 130:
                high.append(f"углеводы +{tot['carbs'] - t['carbs']:.0f} г")
            elif trained and c < 60:
                low.append(f"углеводы −{t['carbs'] - tot['carbs']:.0f} г")
                tips.append("🍚 В день тренировки мало углеводов — гречка, рис, овсянка или картофель помогут восстановиться.")
            else:
                ok.append("углеводы")
    else:
        lines.append(f"🍽 Калории {tot['kcal']:.0f} · белок {tot['protein']:.0f} г (норма неизвестна — заполни профиль)")

    # --- вода ---
    lines.append(f"💧 Вода {water:.0f}/{water_goal:.0f} мл")
    if water >= water_goal:
        ok.append("вода")
    else:
        low.append(f"вода −{water_goal - water:.0f} мл")
        if water < water_goal * 0.75:
            tips.append(f"💧 Выпей ещё {water_goal - water:.0f} мл (≈{(water_goal - water) / nut.GLASS_ML:.0f} стак. по {nut.GLASS_ML} мл) — "
                        "нехватка воды снижает силу и выносливость.")

    # --- сон ---
    if sleep is None:
        lines.append("😴 Сон не записан (напиши: сон 7)")
    else:
        lines.append(f"😴 Сон {fmt_hours(sleep)} (норма {SLEEP_MIN:g}–{SLEEP_MAX:g} ч)")
        if sleep < 6:
            low.append(f"сон −{fmt_kg(round(SLEEP_MIN - sleep, 1))} ч")
            tips.append("😴 Мало сна — мышцы восстанавливаются хуже. Сегодня без рекордов, работай в обычных весах; "
                        "ляг на час раньше.")
        elif sleep < SLEEP_MIN:
            low.append(f"сон −{fmt_kg(round(SLEEP_MIN - sleep, 1))} ч")
            tips.append("😴 Сна чуть меньше нормы — постарайся лечь на 30–60 минут раньше.")
        elif sleep > 10:
            high.append(f"сон +{fmt_kg(round(sleep - SLEEP_MAX, 1))} ч")
        else:
            ok.append("сон")

    lines.append("")
    if ok:
        lines.append("✅ В норме: " + ", ".join(ok))
    if low:
        lines.append("⬇️ Недобор: " + ", ".join(low))
    if high:
        lines.append("⬆️ Перебор: " + ", ".join(high))
    if tips:
        lines.append("")
        lines.append("💡 Что сделать:")
        lines.extend(tips)
    if not low and not high and ok:
        lines.append("\n🔥 Отличный день, всё сбалансировано!")
    if day == now.date() and now.hour < 20 and (low or not food):
        lines.append("\nДень ещё не закончился — недобор можно добрать.")
    lines.append("\nЭто ориентир по общим правилам, не совет врача.")
    return "\n".join(lines)


def week_summary(db: DB, user_id: int, end: date | None = None) -> str:
    end = end or date.today()
    start = end - timedelta(days=6)
    p = db.get_profile(user_id)
    t = nut.targets(p) if p else None
    lines = [f"📋 Итог недели {start:%d.%m}–{end:%d.%m.%Y}", ""]
    tips = []

    ws = [w for w in db.workouts_between(user_id, start, end) if db.workout_sets(w["id"])]
    ton = sum(reports.tonnage(db.workout_sets(w["id"])) for w in ws)
    pws = [w for w in db.workouts_between(user_id, start - timedelta(days=7), start - timedelta(days=1))
           if db.workout_sets(w["id"])]
    pton = sum(reports.tonnage(db.workout_sets(w["id"])) for w in pws)
    line = f"🏋️ Тренировок: {len(ws)}, тоннаж {fmt_kg(ton)} кг"
    if pton:
        line += f" ({(ton - pton) / pton * 100:+.0f}% к прошлой неделе)"
    lines.append(line)
    if not ws:
        tips.append("🏋️ За неделю ни одной тренировки — запланируй ближайшую.")

    days = db.nutrition_days(user_id, start, end)
    if days:
        k = len(days)
        avg = {f: sum(d[f] for d in days) / k for f in ("kcal", "protein", "fat", "carbs", "water")}
        if t:
            lines.append(f"🍽 В среднем: {avg['kcal']:.0f}/{t['kcal']:.0f} ккал · белок {avg['protein']:.0f}/{t['protein']:.0f} г "
                         f"(записей {k} дн.)")
            if avg["protein"] < t["protein"] * 0.9:
                tips.append(f"🥩 Белка в среднем {avg['protein']:.0f} г из {t['protein']:.0f} — добавь творог, яйца, курицу или рыбу "
                            "в каждый основной приём пищи.")
            if avg["kcal"] > t["kcal"] * 1.15:
                tips.append("🍽 Калорий в среднем больше нормы — следи за сладким и вечерними перекусами.")
            elif avg["kcal"] < t["kcal"] * 0.8 and p["goal"] != "loss":
                tips.append("🍽 Калорий в среднем мало — мышцам не из чего расти.")
            water_ok = sum(1 for d in days if d["water"] >= t["water"])
            lines.append(f"💧 Норма воды выполнена: {water_ok} из {k} дн.")
            if water_ok < k / 2:
                tips.append("💧 Воды мало в большинство дней — держи бутылку на виду, напоминания помогут.")
        else:
            lines.append(f"🍽 В среднем: {avg['kcal']:.0f} ккал · белок {avg['protein']:.0f} г")
    else:
        lines.append("🍽 Питание за неделю не записано.")

    sl = db.sleep_between(user_id, start, end)
    if sl:
        avg_s = sum(r["hours"] for r in sl) / len(sl)
        short = sum(1 for r in sl if r["hours"] < SLEEP_MIN)
        lines.append(f"😴 Сон в среднем {fmt_hours(avg_s)}, меньше 7 ч — {short} из {len(sl)} ночей")
        if avg_s < SLEEP_MIN:
            tips.append("😴 Сна в среднем меньше 7 ч — это тормозит рост силы сильнее, чем пропущенная тренировка. "
                        "Сдвинь отбой раньше.")
    else:
        lines.append("😴 Сон за неделю не записан.")

    if tips:
        lines += ["", "💡 Что поправить:"] + tips
    else:
        lines += ["", "🔥 Неделя сбалансирована, так держать!"]
    return "\n".join(lines)
