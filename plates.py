"""Калькулятор блинов: какие блины повесить на каждую сторону грифа."""
from exercises import BARS, PLATES

UNIT = 0.25  # все блины кратны 0,25 кг — считаем в целых «четвертинках»


def plates_per_side(total: float, bar: float = BARS["straight"], plates: dict = PLATES):
    """Возвращает (список блинов на одну сторону, реально получившийся вес).

    Если точный вес собрать нельзя, берётся ближайший меньший.
    Из нескольких вариантов выбирается тот, где меньше всего блинов.
    """
    if total < bar:
        return [], bar
    target = round((total - bar) / 2 / UNIT)
    # sum -> лучший набор блинов (меньше штук)
    best: dict[int, list] = {0: []}
    for w in sorted(plates, reverse=True):
        unit_w = round(w / UNIT)
        for _ in range(plates[w] // 2):  # на одну сторону — половина пар
            for s, combo in list(best.items()):
                ns = s + unit_w
                if ns <= target and (ns not in best or len(best[ns]) > len(combo) + 1):
                    best[ns] = combo + [w]
    reached = max(best)
    combo = sorted(best[reached], reverse=True)
    return combo, bar + 2 * reached * UNIT


def fmt_kg(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def plates_text(total: float, bar_kind: str = "straight") -> str:
    bar = BARS[bar_kind]
    combo, real = plates_per_side(total, bar)
    bar_name = "EZ-гриф" if bar_kind == "ez" else "гриф"
    if not combo:
        return f"🏋️ {fmt_kg(total)} кг: это меньше или равно весу грифа ({bar_name} {fmt_kg(bar)} кг)."
    side = " + ".join(fmt_kg(p) for p in combo)
    text = f"🏋️ {fmt_kg(real)} кг ({bar_name} {fmt_kg(bar)} кг)\nНа каждую сторону: {side}"
    if abs(real - total) > 1e-9:
        text += f"\n⚠️ Ровно {fmt_kg(total)} кг из твоих блинов не собрать, ближайший вес — {fmt_kg(real)} кг."
    return text
