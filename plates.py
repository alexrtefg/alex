"""Калькулятор блинов: какие блины повесить на каждую сторону грифа или гантели."""
from exercises import BARS, PLATES

UNIT = 0.25  # все блины кратны 0,25 кг — считаем в целых «четвертинках»


def plates_per_side(total: float, bar: float = BARS["straight"], plates: dict = PLATES, sides: int = 2):
    """Возвращает (список блинов на одну сторону, реально получившийся вес одного снаряда).

    sides — на сколько сторон делится весь набор блинов:
    2 для штанги, 4 для пары наборных гантелей (у каждой гантели 2 стороны).
    Если точный вес собрать нельзя, берётся ближайший меньший.
    Из нескольких вариантов выбирается тот, где меньше всего блинов.
    """
    if total <= bar:
        return [], bar
    target = round((total - bar) / 2 / UNIT)
    best: dict[int, list] = {0: []}  # сумма на сторону -> набор блинов (меньше штук)
    for w in sorted(plates, reverse=True):
        unit_w = round(w / UNIT)
        for _ in range(plates[w] // sides):
            for s, combo in list(best.items()):
                ns = s + unit_w
                if ns <= target and (ns not in best or len(best[ns]) > len(combo) + 1):
                    best[ns] = combo + [w]
    reached = max(best)
    return sorted(best[reached], reverse=True), bar + 2 * reached * UNIT


def max_weight(bar: float, sides: int = 2) -> float:
    return bar + 2 * sum(w * (n // sides) for w, n in PLATES.items())


def fmt_kg(x: float) -> str:
    return f"{x:g}".replace(".", ",")


BAR_NAMES = {"straight": "гриф", "ez": "EZ-гриф", "dumbbell": "гриф гантели"}


def plates_text(total: float, bar_kind: str = "straight") -> str:
    bar = BARS[bar_kind]
    sides = 4 if bar_kind == "dumbbell" else 2
    combo, real = plates_per_side(total, bar, sides=sides)
    bar_name = BAR_NAMES[bar_kind]
    what = "Пара гантелей по" if bar_kind == "dumbbell" else "🏋️"
    if not combo:
        return f"{fmt_kg(total)} кг: это не больше веса грифа ({bar_name} {fmt_kg(bar)} кг)."
    side = " + ".join(fmt_kg(p) for p in combo)
    text = (f"{what} {fmt_kg(real)} кг ({bar_name} {fmt_kg(bar)} кг)\n"
            f"На каждую сторону{' каждой гантели' if sides == 4 else ''}: {side}")
    if abs(real - total) > 1e-9:
        top = max_weight(bar, sides)
        if total > top:
            text += f"\n⚠️ Больше {fmt_kg(top)} кг из твоих блинов не собрать."
        else:
            text += f"\n⚠️ Ровно {fmt_kg(total)} кг из твоих блинов не собрать, ближайший вес — {fmt_kg(real)} кг."
    return text
