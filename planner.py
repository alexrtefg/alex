"""«🧠 План»: тренировка на сегодня из выбранных групп мышц и твоего инвентаря (правила, без ИИ).

Как бот выбирает упражнения:
- на каждую группу — своё число упражнений (крупные группы больше, мелкие меньше), всего не больше 8 + пресс;
- в крупной группе первое упражнение — базовое и знакомое тебе (чтобы следить за прогрессом), дальше — разнообразие:
  давно не делал или ещё не пробовал — выше в списке;
- не больше двух упражнений с тяжёлой нагрузкой на поясницу;
- если за последние 2 недели писал «болит плечо/колено/поясница», упражнения на этот сустав убираются;
- если спал меньше 6 часов — подходов на один меньше;
- порядок: ноги → спина → грудь → плечи → руки → пресс, базовые раньше изолирующих.
"""
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

import clock

from exercises import BY_KEY, EXERCISES, GROUPS, TEMPLATES, rest_for
from library import JOINT_NAMES, LIB, MAIN_LIFTS, PAIN_WORDS, PATTERN
from plates import fmt_kg

ORDER = ["legs", "back", "chest", "shoulders", "triceps", "biceps", "abs"]
BIG = {"legs", "back", "chest"}
FAMILIAR = set(TEMPLATES["A"]["exercises"]) | set(TEMPLATES["B"]["exercises"])
MAX_MAIN = 8      # упражнений без пресса
MAX_SPINE = 2     # тяжёлых для поясницы


@dataclass
class PlanItem:
    key: str
    sets: int
    reps: str
    rest: int
    hint: str = ""
    why: str = ""


@dataclass
class Plan:
    groups: list
    items: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def minutes(self) -> int:
        return round(sum(i.sets * (45 + i.rest) for i in self.items) / 60)

    @property
    def keys(self) -> list:
        return [i.key for i in self.items]


def quotas(groups: list) -> dict:
    main = [g for g in groups if g != "abs"]
    few = len(main) <= 2
    q = {"chest": 3 if few else 2, "back": 3 if few else 2, "legs": 4 if few else 3,
         "shoulders": 2, "biceps": 2 if few else 1, "triceps": 2 if few else 1}
    q = {g: q[g] for g in main}
    reduce_order = ["biceps", "triceps", "shoulders", "legs", "chest", "back"]
    cap = MAX_MAIN if len(main) <= 4 else MAX_MAIN - 1      # всё тело — до 7 упражнений, чтобы уложиться в час
    while sum(q.values()) > cap:
        for g in reduce_order:
            if g in q and q[g] > 1 and sum(q.values()) > cap:
                q[g] -= 1
        if all(v == 1 for v in q.values()):
            break
    if "abs" in groups:
        q["abs"] = 2
    return q


def recent_pains(db, user_id: int, today: date, days: int = 14) -> set:
    start = today - timedelta(days=days)
    texts = [r["pain"] for r in db.pains_between(user_id, start, today)]
    texts += [r["value"] for r in db.conn.execute(
        "SELECT value FROM settings WHERE user_id=? AND key LIKE 'pain:%' AND key >= ?",
        (user_id, f"pain:{start.isoformat()}")).fetchall() if r["value"]]
    joints = set()
    for t in texts:
        low = t.lower()
        for word, joint in PAIN_WORDS.items():
            if word in low:
                joints.add(joint)
    return joints


def last_done(db, user_id: int) -> dict:
    rows = db.conn.execute(
        "SELECT e.exercise_key k, MAX(w.day) d FROM workouts w JOIN entries e ON e.workout_id=w.id "
        "WHERE w.user_id=? GROUP BY e.exercise_key", (user_id,)).fetchall()
    return {r["k"]: date.fromisoformat(r["d"]) for r in rows}


def weight_hint(db, user_id: int, key: str) -> str:
    hist = db.exercise_history(user_id, key)
    ex, meta = BY_KEY[key], LIB[key]
    if not hist:
        if meta.tool:
            return meta.tool
        if ex.bodyweight:
            return "свой вес"
        return "подбери вес, чтобы оставалось 2 повтора в запасе"
    last_w = hist[-1]["workout_id"]
    rows = [r for r in hist if r["workout_id"] == last_w]
    top = max(rows, key=lambda r: (r["weight"] + r["extra"], r["reps"]))
    effort = next((r["effort"] for r in reversed(rows) if r["effort"]), None)
    mark = {1: " 😌", 2: " 💪", 3: " 🥵"}.get(effort, "")
    if ex.bodyweight:
        base = f"прошлый раз {top['reps']} повт." + (f" +{fmt_kg(top['extra'])} кг" if top["extra"] else "") + mark
        return base + (" → сегодня +1–2 повтора" if effort == 1 else "")
    w = top["weight"]
    base = f"прошлый раз {fmt_kg(w)} кг × {top['reps']}{mark}"
    if effort == 1 and not meta.tool:
        return base + f" → сегодня {fmt_kg(w + 2.5)} кг"
    return base


def build_plan(db, user_id: int, groups: list, seed: int = 0, today: date | None = None) -> Plan:
    today = today or clock.today()
    groups = [g for g in ORDER if g in groups]
    plan = Plan(groups)
    rnd = random.Random(f"{user_id}-{today}-{seed}")
    bad = recent_pains(db, user_id, today)
    done = last_done(db, user_id)
    sleep = db.get_sleep(user_id, today)
    minus = 1 if sleep is not None and sleep < 6 else 0
    if bad:
        plan.notes.append("🤕 Недавно болело: " + ", ".join(JOINT_NAMES[j] for j in bad) +
                          " — упражнения с нагрузкой на это место убраны.")
    if minus:
        plan.notes.append("😴 Сон меньше 6 ч — подходов на один меньше, работай без рекордов.")

    spine_used = 0
    used_patterns: set = set()
    q = quotas(groups)
    for g in groups:
        cands = [e.key for e in EXERCISES if e.group == g and not (set(LIB[e.key].joints) & bad)]
        picked = []
        for slot in range(q.get(g, 0)):
            def score(k):
                m = LIB[k]
                d = done.get(k)
                s = rnd.uniform(0, 4)
                if k in FAMILIAR:
                    s += 3
                s += 5 if d is None and k not in FAMILIAR else (min((today - d).days, 28) / 4 if d else 2)
                if slot == 0 and g in BIG:
                    s += 10 if m.kind == "C" else -20
                    s += 5 if k in FAMILIAR else 0
                    s += 6 if k in MAIN_LIFTS else 0
                if PATTERN.get(k) and PATTERN.get(k) in used_patterns:
                    s -= 8                                   # похожее движение уже есть
                return s
            options = [k for k in cands if k not in picked and not (LIB[k].spine and spine_used >= MAX_SPINE)]
            if not options:
                break
            k = max(options, key=score)
            picked.append(k)
            spine_used += LIB[k].spine
            if PATTERN.get(k):
                used_patterns.add(PATTERN[k])
        picked.sort(key=lambda k: {"C": 0, "I": 1, "A": 2}[LIB[k].kind])
        for k in picked:
            m = LIB[k]
            d = done.get(k)
            why = "новое для тебя" if d is None and k not in FAMILIAR else (
                f"давно не делал ({(today - d).days} дн.)" if d and (today - d).days >= 14 else "")
            plan.items.append(PlanItem(k, max(2, m.sets - minus), m.reps, rest_for(k),
                                       weight_hint(db, user_id, k), why))
    return plan


def plan_text(plan: Plan) -> str:
    names = ", ".join(GROUPS[g].lower() for g in plan.groups)
    lines = [f"🧠 План на сегодня: {names}", f"≈{plan.minutes} мин, упражнений: {len(plan.items)}", ""]
    for i, it in enumerate(plan.items, 1):
        lines.append(f"{i}. {BY_KEY[it.key].name} — {it.sets}×{it.reps}, отдых {it.rest // 60}:{it.rest % 60:02d}")
        extra = " · ".join(x for x in (it.hint, it.why) if x)
        if extra:
            lines.append(f"   {extra}")
    if plan.notes:
        lines.append("")
        lines.extend(plan.notes)
    lines.append("\nВсе подходы — рабочие, оставляй 1–2 повтора в запасе. Техника — кнопка «📖 Техника».")
    return "\n".join(lines)


def technique_text(plan: Plan) -> str:
    lines = ["📖 Техника"]
    for it in plan.items:
        lines.append(f"\n• {BY_KEY[it.key].name}\n  {LIB[it.key].cue}")
    return "\n".join(lines)


def splits_text(db, user_id: int) -> str:
    ws = db.recent_workouts(user_id, 8)
    days = sorted({date.fromisoformat(w["day"]) for w in ws})
    gap = sum((b - a).days for a, b in zip(days, days[1:])) / (len(days) - 1) if len(days) > 2 else 4.5
    gap_s = fmt_kg(round(gap, 1))
    return (
        f"📅 Сплиты — как делить мышцы по тренировкам\n\nТвой ритм: тренировка раз в ~{gap_s} дн.\n\n"
        f"1️⃣ Фулбоди А/Б (всё тело каждую тренировку) — ✅ лучший вариант при твоём ритме\n"
        f"Каждая мышца работает раз в {gap_s} дн. В каждой тренировке: ноги (присед или тяга) + жим + тяга спины "
        "+ плечи или руки + пресс, 6–8 упражнений, тренировки А и Б отличаются упражнениями.\n"
        "Собрать: «🧠 План» → отметь все группы.\n\n"
        f"2️⃣ Верх/низ (как сейчас: А — грудь, спина, руки; Б — ноги)\n"
        f"При твоём ритме каждая мышца работает раз в {fmt_kg(round(gap * 2, 1))} дн. Для поддержания формы — нормально, "
        "для роста маловато. Хорош, если тренироваться каждые 2–3 дня.\n\n"
        "3️⃣ Толкай / тяни / ноги (3 тренировки в неделю)\n"
        "Толкай: грудь, плечи, трицепс. Тяни: спина, бицепс, задняя дельта. Ноги + пресс. "
        "Подходит, если сможешь заниматься 3 раза в неделю.\n\n"
        "В 50 лет важнее всего регулярность, техника и восстановление: сон 7+ ч, белок, "
        "1–2 повтора в запасе, лёгкая неделя раз в 6–8 недель.\n"
        "Это общие рекомендации, не замена тренеру или врачу.")
