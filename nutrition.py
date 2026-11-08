"""Питание и вода: разбор «2 яблока, тарелка гречки, стакан молока», нормы и дневной отчёт."""
import re
from dataclasses import dataclass

from foods import BY_KEY as FOOD_BY_KEY
from foods import FOODS, GLASS_ML, PROTEIN_SUGGESTIONS, SIZES, UNIT_CANON, UNITS, WORD_NUMBERS
from matcher import Matcher, normalize

food_matcher = Matcher(catalog={f.key: [f.name, *f.aliases] for f in FOODS})

ABSOLUTE_UNITS = {"г", "гр", "грамм", "граммов", "грамма", "кг", "мл", "л", "литр", "литра"}
WATER_WORDS = {"вода", "воды", "водички", "водичка", "воду", "чистая вода", "чистой воды", "вода чистая"}
STOP_WORDS = {"съел", "съела", "поел", "поела", "ел", "ела", "еда", "выпил", "выпила", "пил", "примерно",
              "около", "где", "то", "грамм", "граммов", "ещё", "еще", "сегодня", "на", "обед", "ужин", "завтрак",
              "перекус"}
FOOD_PREFIXES = ("еда", "съел", "съела", "поел", "поела", "выпил", "выпила", "завтрак", "обед", "ужин", "перекус")

ACTIVITY = 1.375          # тренировки 1–2 раза в неделю + обычная бытовая активность
GOALS = {"maintain": ("поддержание веса", 1.0), "loss": ("снижение веса", 0.85), "gain": ("набор массы", 1.10)}
PROTEIN_PER_KG = 1.6      # г белка на кг веса: для сохранения и роста мышц при силовых тренировках
FAT_PER_KG = 1.0
WATER_GOAL_ML = 2000


@dataclass
class FoodItem:
    raw: str
    kind: str = "food"        # food | water
    key: str | None = None
    grams: float = 0
    status: str = "ok"        # ok | guess | none
    note: str = ""
    score: float = 0

    @property
    def food(self):
        return FOOD_BY_KEY.get(self.key)


def _num(tok: str) -> float | None:
    try:
        return float(tok)
    except ValueError:
        return WORD_NUMBERS.get(tok)


def _prepare(text: str) -> str:
    s = text.lower().replace("ё", "е")
    s = re.sub(r"(\d+(?:[.,]\d+)?)\s*[-–—]\s*(\d+(?:[.,]\d+)?)",              # 150-200 -> 175
               lambda m: f"{(float(m.group(1).replace(',', '.')) + float(m.group(2).replace(',', '.'))) / 2:g}", s)
    s = re.sub(r"(?<=\d),(?=\d)", ".", s)                                     # 1,5 -> 1.5
    s = re.sub(r"(\d)([a-zа-я])", r"\1 \2", s)                                # 150г -> 150 г
    s = re.sub(r"\bпол(?=(" + "|".join(UNITS) + r")\b)", "пол ", s)           # полтарелки -> пол тарелки
    return s


# Названия продуктов, внутри которых есть «и» (их нельзя резать по «и»).
_ALIASES_WITH_AND = sorted((a for a in food_matcher.alias_to_key if " и " in a), key=len, reverse=True)


def split_items(text: str) -> list[str]:
    s = _prepare(text)
    for p in FOOD_PREFIXES:
        s = re.sub(rf"^\s*{p}\b[:\s]*", "", s)
    for a in _ALIASES_WITH_AND:                       # «бутерброд с маслом и сыром» — один продукт
        s = s.replace(a, a.replace(" и ", " и_ "))
    s = re.sub(r"(бутерброд\w*\s+с\s+\w+)\s+и\s+(?=[а-я])", r"\1 и_ ", s)   # «2 бутерброда с маслом и сыром»
    # «творог 18», «творог 9%» — это жирность, а не количество
    s = re.sub(r"\b(творог\w*)\s+(5|9|18)\s*(%|процент\w*)?(?=[\s,;]|$)", r"\1_\2", s)
    parts = re.split(r"[,;\n]+|\s\+\s|\sи\s", s)
    return [p.replace(" и_ ", " и ").strip() for p in parts if p.strip()]


def _is_exact(segment: str) -> bool:
    """Есть ли фраза целиком (без цифр и единиц) в справочнике, например «кофе с молоком»."""
    words = [t for t in re.sub(r"[^\w\s.]", " ", segment.lower().replace("ё", "е")).split()
             if _num(t) is None and t not in UNIT_CANON and t not in ABSOLUTE_UNITS and t not in SIZES
             and t not in STOP_WORDS]
    return " ".join(words) in food_matcher.alias_to_key


def parse_item(segment: str) -> FoodItem:
    tokens = re.sub(r"[^\w\s.]", " ", segment.lower().replace("ё", "е")).split()
    item = FoodItem(raw=segment)
    numbers, size, name = [], 1.0, []
    abs_grams = None
    unit = None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        n = _num(t)
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if n is not None:
            if nxt in ABSOLUTE_UNITS:
                abs_grams = n * UNITS[nxt]
                i += 2
                continue
            if nxt in UNIT_CANON:
                unit, numbers = UNIT_CANON[nxt], [n]
                i += 2
                continue
            numbers.append(n)
        elif t in ABSOLUTE_UNITS:
            abs_grams = abs_grams or UNITS[t]   # «литр воды» без числа
        elif t in UNIT_CANON:
            unit = UNIT_CANON[t]
        elif t in SIZES:
            size = SIZES[t]
        elif t not in STOP_WORDS:
            name.append(t)
        i += 1

    name_s = " ".join(name)
    count = numbers[0] if numbers else 1

    if name_s in WATER_WORDS or (name and name[0] in WATER_WORDS and len(name) <= 2):
        item.kind, item.key = "water", "water"
        if abs_grams is not None:
            item.grams = abs_grams
        elif unit:
            item.grams = count * (UNITS.get(unit) or GLASS_ML)
        elif numbers:
            n = numbers[0]
            # 500 -> мл; 1.5 -> литры; 2 -> стаканы
            item.grams = n if n >= 50 else (n * 1000 if n != int(n) else n * GLASS_ML)
        else:
            item.grams = GLASS_ML
        return item

    res = food_matcher.match(name_s)
    item.score = res.score
    if res.status == "none":
        item.status = "none"
        return item
    if res.status == "ask":
        item.status, item.key = "guess", res.options[0][0]
    else:
        item.key = res.key
    food = item.food
    portion = food.portions.get("порция", 100)
    if abs_grams is not None:
        item.grams = abs_grams
    elif unit:
        per = food.portions.get(unit) or (portion if unit in ("шт", "порция") else UNITS.get(unit)) or portion
        item.grams = count * per * size
    elif numbers:
        n = numbers[0]
        if n >= 20:
            item.grams = n                              # «гречка 200» — граммы
        else:
            item.grams = n * food.portions.get("шт", portion) * size
    else:
        item.grams = portion * size
    item.grams = round(item.grams)
    return item


def parse_food_text(text: str) -> list[FoodItem]:
    items = []
    for seg in split_items(text):
        # «скумбрия с картошкой» -> скумбрия + картошка; но «кофе с молоком» остаётся целым
        whole = parse_item(seg)
        if " с " in seg and not _is_exact(seg) and not (whole.status == "ok" and whole.score >= 90):
            parts = [parse_item(p) for p in seg.split(" с ") if p.strip()]
            if len(parts) > 1 and all(p.status == "ok" for p in parts):
                items.extend(parts)
                continue
        items.append(whole)
    return items


def nutrients(food_key: str, grams: float) -> dict:
    f = FOOD_BY_KEY[food_key]
    k = grams / 100
    return dict(kcal=f.kcal * k, protein=f.protein * k, fat=f.fat * k, carbs=f.carbs * k)


def looks_like_food(text: str) -> bool:
    low = text.lower().strip()
    if low.startswith(FOOD_PREFIXES) or low.startswith(("вода", "воды")):
        return True
    if re.search(r"\d\s*[хx*×]\s*\d", low):   # 4х10х60 — это подходы, а не еда
        return False
    items = parse_food_text(text)
    return bool(items) and all(i.status == "ok" for i in items)


# ---------- нормы ----------

def parse_profile(text: str) -> dict:
    """«профиль 85 180 м 50 набор» -> {weight, height, sex, age, goal}."""
    s = normalize(_prepare(text)).replace(",", " ")
    tokens = s.split()
    if tokens and tokens[0] == "профиль":
        tokens = tokens[1:]
    nums = [float(t) for t in tokens if re.fullmatch(r"\d+(\.\d+)?", t)]
    if len(nums) < 3:
        raise ValueError("Формат: профиль ВЕС РОСТ ПОЛ ВОЗРАСТ [цель]\nПример: профиль 85 180 м 50")
    weight, height, age = nums[0], nums[1], nums[2]
    if not (35 <= weight <= 250 and 120 <= height <= 230 and 14 <= age <= 100):
        raise ValueError("Проверь цифры: вес в кг, рост в см, возраст в годах. Пример: профиль 85 180 м 50")
    sex = "f" if any(t in ("ж", "жен", "женщина", "женский") for t in tokens) else "m"
    goal = "maintain"
    if any(t.startswith(("набор", "масс")) for t in tokens):
        goal = "gain"
    elif any(t.startswith(("похуд", "сушк", "сниж", "сброс")) for t in tokens):
        goal = "loss"
    return dict(weight=weight, height=height, sex=sex, age=int(age), goal=goal)


SEX_WORDS = {"м", "муж", "мужчина", "мужской", "ж", "жен", "женщина", "женский"}
PROFILE_WORDS = SEX_WORDS | {"профиль", "кг", "см", "лет", "год", "года", "вес", "рост", "возраст", "цель",
                             "набор", "массы", "масса", "похудение", "сушка", "снижение", "поддержание"}


def looks_like_profile(text: str) -> bool:
    """«76 175 м 50» — профиль без слова «профиль»: 3 числа + пол, других слов нет."""
    tokens = normalize(_prepare(text)).replace(",", " ").split()
    nums = [t for t in tokens if re.fullmatch(r"\d+(\.\d+)?", t)]
    words = [t for t in tokens if t not in nums]
    return len(nums) == 3 and any(t in SEX_WORDS for t in words) and all(t in PROFILE_WORDS for t in words)


def targets(p) -> dict:
    """Норма на день. p — профиль (weight, height, sex, age, goal, water_goal)."""
    bmr = 10 * p["weight"] + 6.25 * p["height"] - 5 * p["age"] + (5 if p["sex"] == "m" else -161)
    kcal = bmr * ACTIVITY * GOALS[p["goal"]][1]
    protein = PROTEIN_PER_KG * p["weight"]
    fat = FAT_PER_KG * p["weight"]
    carbs = max(0.0, (kcal - protein * 4 - fat * 9) / 4)
    return dict(kcal=kcal, protein=protein, fat=fat, carbs=carbs, bmr=bmr,
                water=p["water_goal"] if "water_goal" in p.keys() and p["water_goal"] else WATER_GOAL_ML)


def bar(value: float, goal: float, width: int = 10) -> str:
    frac = 0 if goal <= 0 else min(1.0, value / goal)
    full = round(frac * width)
    return "▓" * full + "░" * (width - full)


def profile_text(p) -> str:
    t = targets(p)
    sex = "мужской" if p["sex"] == "m" else "женский"
    return (f"👤 Профиль: {p['weight']:g} кг, {p['height']:g} см, {sex}, {p['age']} лет\n"
            f"Цель: {GOALS[p['goal']][0]}\n\n"
            f"Норма на день (примерно):\n"
            f"• Калории: {t['kcal']:.0f} ккал (обмен покоя {t['bmr']:.0f} × активность {ACTIVITY})\n"
            f"• Белки: {t['protein']:.0f} г ({PROTEIN_PER_KG} г на кг веса)\n"
            f"• Жиры: {t['fat']:.0f} г\n"
            f"• Углеводы: {t['carbs']:.0f} г\n"
            f"• Чистая вода: {t['water'] / 1000:g} л\n\n"
            f"Это общий ориентир (формула Миффлина — Сан Жеора), а не назначение врача.")


def item_line(name: str, grams: float, n: dict) -> str:
    return f"{name} — {grams:.0f} г · {n['kcal']:.0f} ккал · Б {n['protein']:.0f} · Ж {n['fat']:.0f} · У {n['carbs']:.0f}"


def day_report(rows, water_ml: float, profile, title: str) -> str:
    lines = [f"🍽 {title}", ""]
    tot = dict(kcal=0.0, protein=0.0, fat=0.0, carbs=0.0)
    for i, r in enumerate(rows, 1):
        f = FOOD_BY_KEY.get(r["food_key"])
        lines.append(f"{i}. " + item_line(f.name if f else r["food_key"], r["grams"], r))
        for k in tot:
            tot[k] += r[k]
    if not rows:
        lines.append("Еды пока не записано.")
    lines.append("")
    t = targets(profile) if profile else None
    names = (("kcal", "Калории", "ккал"), ("protein", "Белки", "г"), ("fat", "Жиры", "г"), ("carbs", "Углеводы", "г"))
    for k, title_k, unit in names:
        if t:
            pct = tot[k] / t[k] * 100 if t[k] else 0
            line = f"{title_k}: {tot[k]:.0f} / {t[k]:.0f} {unit} {bar(tot[k], t[k])} {pct:.0f}%"
            gap = t[k] - tot[k]
            if k == "protein" and gap > 5:
                line += f" — не хватает {gap:.0f} г"
            if k == "kcal" and pct > 110:
                line += " — перебор"
            lines.append(line)
        else:
            lines.append(f"{title_k}: {tot[k]:.0f} {unit}")
    goal_w = t["water"] if t else WATER_GOAL_ML
    left = goal_w - water_ml
    w_line = f"💧 Вода: {water_ml:.0f} / {goal_w:.0f} мл {bar(water_ml, goal_w)}"
    w_line += f" — осталось {left:.0f} мл (≈{left / GLASS_ML:.0f} стак. по {GLASS_ML} мл)" if left > 0 else " ✅"
    lines.append(w_line)
    if t and t["protein"] - tot["protein"] > 15:
        gap = t["protein"] - tot["protein"]
        tips = []
        for key in PROTEIN_SUGGESTIONS[:3]:
            f = FOOD_BY_KEY[key]
            g = f.portions.get("порция", 100)
            tips.append(f"{f.name.lower()} {g} г (+{f.protein * g / 100:.0f} г)")
        lines.append(f"\n💡 Добрать белок ({gap:.0f} г): " + ", ".join(tips))
    if not profile:
        lines.append("\nЧтобы видеть норму, заполни профиль: профиль 85 180 м 50")
    return "\n".join(lines)
