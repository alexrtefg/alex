"""Замеры тела: талия, грудь, бицепс, бедро, шея (см)."""
import re

from plates import fmt_kg

PARTS = {"waist": "Талия", "chest": "Грудь", "biceps": "Бицепс", "thigh": "Бедро", "neck": "Шея"}
WORDS = {
    "талия": "waist", "талию": "waist", "талии": "waist", "живот": "waist", "пояс": "waist",
    "грудь": "chest", "груди": "chest", "грудная": "chest",
    "бицепс": "biceps", "бицуха": "biceps", "рука": "biceps", "руки": "biceps",
    "бедро": "thigh", "бедра": "thigh", "нога": "thigh", "ноги": "thigh",
    "шея": "neck", "шеи": "neck",
}
RANGES = {"waist": (50, 160), "chest": (60, 170), "biceps": (20, 60), "thigh": (35, 95), "neck": (28, 60)}
HOWTO = ("Напиши в одну строку, например:\n"
         "талия 88 грудь 102 бицепс 38 бедро 58 шея 40\n"
         "Мерь утром, натощак, сантиметровой лентой: талию — на уровне пупка, грудь — по соскам, "
         "бицепс — напряжённый в самой широкой части, бедро — посередине.")


def parse_measures(text: str) -> dict:
    """«талия 88, грудь 102 см» -> {"waist": 88, "chest": 102}. Значения вне разумных пределов отбрасываются."""
    s = text.lower().replace("ё", "е").replace(",", ".")
    out = {}
    for word, num in re.findall(r"([а-я]+)\s*[:\-=]?\s*(\d+(?:\.\d+)?)", s):
        part = WORDS.get(word)
        if part:
            v = float(num)
            lo, hi = RANGES[part]
            if lo <= v <= hi:
                out[part] = v
    return out


def looks_like_measures(text: str) -> bool:
    """Сообщение целиком состоит из замеров: «талия 88 бедро 58» (а не «бедро 150» — это куриное бедро)."""
    s = text.lower().replace("ё", "е").replace(",", ".")
    tokens = re.findall(r"[а-я]+|\d+(?:\.\d+)?", s)
    words = [t for t in tokens if not t[0].isdigit() and t != "см"]
    if not words or not all(w in WORDS for w in words):
        return False
    return len(parse_measures(text)) == len(words)


def history_text(db, user_id: int) -> str:
    lines = ["📏 Замеры тела (см)"]
    any_data = False
    for part, title in PARTS.items():
        hist = db.measure_history(user_id, part)
        if not hist:
            continue
        any_data = True
        last = hist[-1]
        line = f"• {title}: {fmt_kg(last['value'])}"
        if len(hist) > 1:
            first = hist[0]
            d = last["value"] - first["value"]
            line += (f" (было {fmt_kg(first['value'])} на {first['day'][8:10]}.{first['day'][5:7]}.{first['day'][:4]}, "
                     f"{'+' if d >= 0 else '−'}{fmt_kg(abs(round(d, 1)))})")
        lines.append(line)
    if not any_data:
        lines.append("Пока нет замеров.")
    lines.append("")
    lines.append(HOWTO)
    return "\n".join(lines)
