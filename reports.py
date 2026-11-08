"""Текстовые отчёты: тренировка, период, упражнение."""
from collections import OrderedDict
from datetime import date, timedelta

import clock

from db import DB
from exercises import BY_KEY, GROUPS, TEMPLATES
from plates import fmt_kg

MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
          "сентябрь", "октябрь", "ноябрь", "декабрь"]


def ddmmyyyy(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d.%m.%Y")


def ex_name(key: str) -> str:
    return BY_KEY[key].name if key in BY_KEY else key


EFFORT = {1: "😌 легко", 2: "💪 норм", 3: "🥵 тяжело"}


def body(r) -> float:
    """Вес тела, входящий в подход (подтягивания, брусья, гиперэкстензия); у старых записей 0."""
    try:
        return r["body"] or 0.0
    except (KeyError, IndexError):
        return 0.0


def load(r) -> float:
    """Сколько килограммов реально поднято за один повтор: снаряд + отягощение + вес тела."""
    return r["weight"] + r["extra"] + body(r)


def tonnage(rows) -> float:
    return sum(r["reps"] * load(r) for r in rows)


def e1rm(weight: float, reps: int) -> float:
    """Расчётный максимум на 1 повтор (формула Эпли)."""
    return weight if reps == 1 else weight * (1 + reps / 30)


def fmt_sets(rows) -> str:
    """Подходы коротко: «4×10 по 60 кг», «60 кг×10, 60 кг×8», «3×12 (свой вес)», «3×8 +16 кг».

    Подходы×повторы, потом вес — так не путается с вводом «подходы вес повторы».
    """
    def one(r):
        w = r["weight"] + r["extra"]
        return (r["reps"], r["weight"], r["extra"], w)

    items = [one(r) for r in rows]
    same = all(i[:3] == items[0][:3] for i in items)
    reps, weight, extra, _ = items[0]
    if same:
        s = f"{len(items)}×{reps}"
        if weight:
            s += f" по {fmt_kg(weight)} кг"
        if extra:
            s += f" +{fmt_kg(extra)} кг"
        if not weight and not extra:
            s += " (свой вес)"
        return s
    parts = []
    for reps, weight, extra, _ in items:
        if weight:
            p = f"{fmt_kg(weight)} кг×{reps}"
        else:
            p = f"{reps} раз"
        if extra:
            p += f"(+{fmt_kg(extra)})"
        parts.append(p)
    return ", ".join(parts)


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    return one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many


def human_sets(rows) -> str:
    """«3 подхода × 12 раз × 16 кг» — понятная расшифровка записи (если подходы одинаковые)."""
    first = rows[0]
    if not all((r["reps"], r["weight"], r["extra"]) == (first["reps"], first["weight"], first["extra"]) for r in rows):
        return fmt_sets(rows)
    n, reps = len(rows), first["reps"]
    s = f"{n} {plural(n, 'подход', 'подхода', 'подходов')} × {reps} {plural(reps, 'раз', 'раза', 'раз')}"
    if first["weight"]:
        s += f" × {fmt_kg(first['weight'])} кг"
    if first["extra"]:
        s += f" +{fmt_kg(first['extra'])} кг"
    if not first["weight"] and not first["extra"]:
        s += " (свой вес)"
    return s


def group_by_exercise(rows) -> "OrderedDict[str, list]":
    out: OrderedDict = OrderedDict()
    for r in rows:
        out.setdefault(r["exercise_key"], []).append(r)
    return out


def workout_report(db: DB, workout_id: int, title: str = "📅 Тренировка") -> str:
    w = db.get_workout(workout_id)
    if not w:
        return "Тренировка не найдена."
    rows = db.workout_sets(workout_id)
    head = f"{title} {ddmmyyyy(w['day'])}\n{TEMPLATES[w['wtype']]['title']}"
    if "wellbeing" in w.keys() and w["wellbeing"]:
        head += f"\nСамочувствие: {w['wellbeing']}/5"
    if "pain" in w.keys() and w["pain"]:
        head += f"\n🤕 Болит: {w['pain']}"
    if not rows:
        return head + "\n\nПока нет записей."
    lines = [head, ""]
    for i, (key, ex_rows) in enumerate(group_by_exercise(rows).items(), 1):
        t = tonnage(ex_rows)
        line = f"{i}. {ex_name(key)} — {fmt_sets(ex_rows)}"
        if t:
            line += f" (тоннаж {fmt_kg(t)} кг)"
        efforts = [r["effort"] for r in ex_rows if "effort" in r.keys() and r["effort"]]
        if efforts:
            line += f" {EFFORT[efforts[-1]].split()[0]}"
        lines.append(line)
    n_ex = len(group_by_exercise(rows))
    reps = sum(r["reps"] for r in rows)
    lines.append("")
    lines.append(f"Итого: упражнений {n_ex}, подходов {len(rows)}, повторов {reps}, "
                 f"тоннаж {fmt_kg(tonnage(rows))} кг")
    return "\n".join(lines)


def last_result_text(db: DB, user_id: int, key: str, current_workout: int) -> str | None:
    hist = db.exercise_history(user_id, key, exclude_workout=current_workout)
    if not hist:
        return None
    last_w = hist[-1]["workout_id"]
    rows = [r for r in hist if r["workout_id"] == last_w]
    return f"прошлый раз ({ddmmyyyy(rows[0]['day'])}): {fmt_sets(rows)}"


def period_bounds(kind: str, today: date | None = None) -> tuple[date, date, str]:
    today = today or clock.today()
    if kind == "week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6), f"Неделя {start:%d.%m}–{start + timedelta(days=6):%d.%m.%Y}"
    if kind == "month":
        start = today.replace(day=1)
        nxt = (start + timedelta(days=32)).replace(day=1)
        return start, nxt - timedelta(days=1), f"{MONTHS[today.month - 1].capitalize()} {today.year}"
    if kind == "year":
        return date(today.year, 1, 1), date(today.year, 12, 31), f"{today.year} год"
    raise ValueError(kind)


def previous_bounds(kind: str, start: date) -> tuple[date, date]:
    if kind == "week":
        return start - timedelta(days=7), start - timedelta(days=1)
    if kind == "month":
        end = start - timedelta(days=1)
        return end.replace(day=1), end
    return date(start.year - 1, 1, 1), date(start.year - 1, 12, 31)


def _period_stats(db: DB, user_id: int, start: date, end: date):
    workouts = db.workouts_between(user_id, start, end)
    all_rows = []
    for w in workouts:
        for r in db.workout_sets(w["id"]):
            all_rows.append((w, r))
    return workouts, all_rows


def period_report(db: DB, user_id: int, kind: str, today: date | None = None) -> str:
    start, end, title = period_bounds(kind, today)
    workouts, rows = _period_stats(db, user_id, start, end)
    if not workouts:
        return f"📊 {title}\nТренировок не было."
    sets_only = [r for _, r in rows]
    total = tonnage(sets_only)
    lines = [f"📊 {title}", ""]
    cnt = {t: sum(1 for w in workouts if w["wtype"] == t) for t in ("A", "B", "C")}
    kinds = f"А: {cnt['A']}, Б: {cnt['B']}" + (f", своя: {cnt['C']}" if cnt["C"] else "")
    lines.append(f"Тренировок: {len(workouts)} ({kinds})")
    days = sorted({date.fromisoformat(w["day"]) for w in workouts})   # несколько записей в один день = один день
    if len(days) > 1:
        gaps = [(b - a).days for a, b in zip(days, days[1:])]
        lines.append(f"Средний интервал: {fmt_kg(round(sum(gaps) / len(gaps), 1))} дн.")
    lines.append(f"Подходов: {len(sets_only)}, тоннаж: {fmt_kg(total)} кг")

    p_start, p_end = previous_bounds(kind, start)
    _, prev_rows = _period_stats(db, user_id, p_start, p_end)
    prev_total = tonnage([r for _, r in prev_rows])
    if prev_total:
        diff = (total - prev_total) / prev_total * 100
        lines.append(f"К прошлому периоду: {diff:+.0f}% тоннажа")

    by_group: dict[str, float] = {}
    for r in sets_only:
        g = BY_KEY[r["exercise_key"]].group if r["exercise_key"] in BY_KEY else "other"
        by_group[g] = by_group.get(g, 0) + r["reps"] * load(r)
    if total:
        lines.append("")
        lines.append("По группам (доля тоннажа):")
        for g, t in sorted(by_group.items(), key=lambda kv: -kv[1]):
            if t:
                lines.append(f"• {GROUPS.get(g, g)}: {t / total * 100:.0f}%")

    lines.append("")
    lines.append("По упражнениям (подходы · лучший вес · тоннаж):")
    for key, ex_rows in group_by_exercise(sets_only).items():
        best = max(r["weight"] + r["extra"] for r in ex_rows)
        best_txt = f"{fmt_kg(best)} кг" if best else "свой вес"
        reps_total = sum(r["reps"] for r in ex_rows)
        t = tonnage(ex_rows)
        tail = f"{fmt_kg(t)} кг" if t else f"{reps_total} повт."
        lines.append(f"• {ex_name(key)}: {len(ex_rows)} · {best_txt} · {tail}")
    return "\n".join(lines)


def exercise_report(db: DB, user_id: int, key: str, last_n: int = 10) -> str:
    hist = db.exercise_history(user_id, key)
    name = ex_name(key)
    if not hist:
        return f"📈 {name}\nЗаписей пока нет."
    by_w: OrderedDict = OrderedDict()
    for r in hist:
        by_w.setdefault((r["workout_id"], r["day"]), []).append(r)
    lines = [f"📈 {name}", ""]
    weighted = any(r["weight"] + r["extra"] for r in hist)
    for (wid, day), rows in list(by_w.items())[-last_n:]:
        line = f"{ddmmyyyy(day)}: {fmt_sets(rows)}"
        if weighted:
            best = max(e1rm(r["weight"] + r["extra"], r["reps"]) for r in rows)
            line += f" · 1ПМ≈{fmt_kg(round(best))}"
        lines.append(line)
    lines.append("")
    if weighted:
        top = max(hist, key=lambda r: (r["weight"] + r["extra"], r["reps"]))
        lines.append(f"🏆 Рекорд веса: {fmt_kg(top['weight'] + top['extra'])} кг × {top['reps']} "
                     f"({ddmmyyyy(top['day'])})")
        best_e = max(hist, key=lambda r: e1rm(r["weight"] + r["extra"], r["reps"]))
        lines.append(f"🏆 Лучший расчётный 1ПМ: {fmt_kg(round(e1rm(best_e['weight'] + best_e['extra'], best_e['reps']), 1))} кг "
                     f"({ddmmyyyy(best_e['day'])})")
    else:
        top = max(hist, key=lambda r: r["reps"])
        lines.append(f"🏆 Рекорд повторов: {top['reps']} ({ddmmyyyy(top['day'])})")
    lines.append(f"Тренировок с этим упражнением: {len(by_w)}")
    return "\n".join(lines)
