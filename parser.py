"""Разбор строки вида «жим лежа 4х10х60» на название и подходы."""
import re
from dataclasses import dataclass

from matcher import normalize

NUM = r"\d+(?:\.\d+)?"


class ParseError(ValueError):
    pass


@dataclass
class SetRow:
    reps: int
    weight: float = 0.0   # вес снаряда, кг (для штанги — вместе с грифом)
    extra: float = 0.0    # отягощение на поясе (подтягивания, брусья)


def prepare(text: str) -> str:
    s = normalize(text)
    s = re.sub(r"(?<=\d),(?=\d)", ".", s)                  # 57,5 -> 57.5
    s = re.sub(r"(?<=\d)\s*[xх*×]\s*(?=\d)", "х", s)        # 4 x 10 * 60 -> 4х10х60
    s = re.sub(r"\+\s+(?=\d)", "+", s)                     # + 16 -> +16
    s = re.sub(r"(?<=\d)\s*кг\b", "", s)                    # 60кг -> 60
    return re.sub(r"\s+", " ", s).strip()


def split_name(text: str) -> tuple[str, str]:
    """Возвращает (название, часть с цифрами)."""
    s = prepare(text)
    m = re.search(r"(^|\s)(?=[+\d])", s)
    if not m:
        return s, ""
    return s[:m.start()].strip(), s[m.start():].strip()


def _f(x: str | None) -> float:
    return float(x) if x else 0.0


def parse_sets(numbers: str, bodyweight: bool = False) -> list[SetRow]:
    s = numbers.strip()
    if not s:
        raise ParseError("Не вижу цифр. Пример: жим лежа 4х10х60")

    # 4х10х60 | 4х10 60 | 3х8 +16 | 3х8х16 (для подтягиваний 16 = отягощение)
    m = re.fullmatch(rf"(\d+)х(\d+)(?:х({NUM}))?(?:\s+({NUM}))?(?:\s*\+({NUM}))?", s)
    if m:
        sets, reps = int(m.group(1)), int(m.group(2))
        weight = _f(m.group(3) or m.group(4))
        extra = _f(m.group(5))
        if bodyweight and weight and not extra:
            weight, extra = 0.0, weight
        rows = [SetRow(reps, weight, extra) for _ in range(sets)]
        return _check(rows)

    # 3 80 8 — подходы, вес, повторы (главный короткий формат; для упражнений с собственным весом
    #          вес = отягощение: «гипер 3 20 15», «подтягивания 3 0 10»)
    # 60 10 10 8 8 — вес, потом повторы каждого подхода | собственный вес: 12 10 8 [+16]
    m = re.fullmatch(rf"({NUM}(?:\s+{NUM})*)(?:\s*\+({NUM}))?", s)
    if m:
        nums = m.group(1).split()
        extra = _f(m.group(2))
        if len(nums) == 3 and nums[0].isdigit() and nums[2].isdigit() and 1 <= int(nums[0]) <= 10:
            sets, w, reps = int(nums[0]), float(nums[1]), int(nums[2])
            row = SetRow(reps, 0.0, w + extra) if bodyweight else SetRow(reps, w, extra)
            return _check([SetRow(row.reps, row.weight, row.extra) for _ in range(sets)])
        if bodyweight:
            reps_list, weight = nums, 0.0
        else:
            if len(nums) < 2:
                raise ParseError("Нужны подходы, вес и повторы. Пример: 3 80 8 (3 подхода по 80 кг на 8 раз)")
            weight, reps_list = float(nums[0]), nums[1:]
        rows = []
        for r in reps_list:
            if "." in r:
                raise ParseError(f"Повторы должны быть целыми: {r}")
            rows.append(SetRow(int(r), weight, extra))
        return _check(rows)

    raise ParseError("Не понял цифры. Формат: подходы вес повторы — например 3 80 8 или 1 75 10")


def _check(rows: list[SetRow]) -> list[SetRow]:
    if not rows or len(rows) > 20:
        raise ParseError("Подходов должно быть от 1 до 20")
    for r in rows:
        if not 1 <= r.reps <= 500:
            raise ParseError("Повторов должно быть от 1 до 500")
        if not 0 <= r.weight <= 400 or not 0 <= r.extra <= 200:
            raise ParseError("Странный вес, проверь цифры")
    return rows


def parse_triset(text: str, keywords, default_rounds: int, default_reps: list[int]):
    """«пресс», «пресс 2», «пресс 2 30 110 105» -> (кругов, [повторы...]). Иначе None."""
    name, nums = split_name(text)
    if name not in keywords:
        return None
    parts = nums.split()
    if not all(p.isdigit() for p in parts):
        return None
    if len(parts) == 0:
        return default_rounds, list(default_reps)
    if len(parts) == 1:
        return int(parts[0]), list(default_reps)
    if len(parts) == 1 + len(default_reps):
        return int(parts[0]), [int(p) for p in parts[1:]]
    raise ParseError(f"Формат: пресс [кругов] [повторы {len(default_reps)} упражнений], "
                     f"например: пресс 2 {' '.join(map(str, default_reps))}")
