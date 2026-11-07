"""Умный поиск упражнения по названию с опечатками (без ИИ, сравнение строк)."""
import re
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from exercises import EXERCISES

ACCEPT_SCORE = 75   # с такой похожестью принимаем сразу
ASK_SCORE = 60      # от этого значения предлагаем варианты кнопками
MIN_GAP = 5         # насколько лучший вариант должен опережать второй
TEMPLATE_BONUS = 4  # упражнения текущей тренировки ищутся в первую очередь


def similarity(a: str, b: str, **_) -> float:
    """Похожесть строк 0..100: смесь «частичного» и «полного» сравнения.

    WRatio хорошо ловит опечатки и сокращения, но слишком любит длинные названия,
    которые содержат запрос целиком. ratio сравнивает строки целиком и это уравновешивает.
    """
    return 0.5 * fuzz.WRatio(a, b) + 0.5 * fuzz.ratio(a, b)


def normalize(text: str) -> str:
    s = text.lower().replace("ё", "е")
    s = re.sub(r"\b30\s*(°|градус\w*|гр\b)", " ", s)   # «30°» — часть названия, а не число
    s = s.replace("°", " ")
    s = re.sub(r"(наклон\w*)\s+30\b(?!\s*[хx*×])", r"\1", s)
    s = re.sub(r"[^\w\s+.,*×]", " ", s)               # убрать кавычки, скобки, тире
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class MatchResult:
    status: str                  # "ok" | "ask" | "none"
    key: str | None = None       # найденное упражнение (status == ok)
    options: list | None = None  # [(key, score), ...] для кнопок (status == ask)


class Matcher:
    """Поиск по справочнику. catalog: {код: [название, синоним, ...]}; по умолчанию — упражнения."""

    def __init__(self, learned_aliases: dict | None = None, catalog: dict | None = None):
        if catalog is None:
            catalog = {ex.key: [ex.name, *ex.aliases] for ex in EXERCISES}
        self.alias_to_key: dict[str, str] = {}
        for key, names in catalog.items():
            for n in names:
                self.alias_to_key[normalize(n)] = key
        for alias, key in (learned_aliases or {}).items():
            self.learn(alias, key)

    def learn(self, alias: str, key: str):
        alias = normalize(alias)
        if alias:
            self.alias_to_key[alias] = key

    def is_generic(self, alias: str) -> bool:
        """Слово вроде «бицепс» или «тяга» входит в названия нескольких упражнений —
        его нельзя запоминать как имя одного из них."""
        a = normalize(alias)
        keys = {k for al, k in self.alias_to_key.items() if a in al}
        return len(keys) >= 2

    def match(self, query: str, prefer: list | None = None) -> MatchResult:
        q = normalize(query)
        if not q:
            return MatchResult("none")
        if q in self.alias_to_key:
            return MatchResult("ok", self.alias_to_key[q])

        prefer = set(prefer or [])
        found = process.extract(q, list(self.alias_to_key), scorer=similarity, limit=30)
        best: dict[str, float] = {}
        for alias, score, _ in found:
            key = self.alias_to_key[alias]
            if key in prefer:
                score = min(100.0, score + TEMPLATE_BONUS)
            best[key] = max(best.get(key, 0), score)
        ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
        if not ranked or ranked[0][1] < ASK_SCORE:
            return MatchResult("none")
        top_key, top = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0
        if top >= ACCEPT_SCORE and top - second >= MIN_GAP:
            return MatchResult("ok", top_key)
        return MatchResult("ask", options=[kv for kv in ranked[:4] if kv[1] >= max(ASK_SCORE, top - 15)])
