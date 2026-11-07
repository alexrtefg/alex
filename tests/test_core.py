import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import DB  # noqa: E402
from exercises import ABS_TRISET, BY_KEY, TEMPLATES  # noqa: E402
from matcher import Matcher  # noqa: E402
from parser import ParseError, parse_sets, parse_triset, split_name  # noqa: E402
from plates import max_weight, plates_per_side  # noqa: E402
import nutrition as nut  # noqa: E402
import reports  # noqa: E402

m = Matcher()


@pytest.mark.parametrize("query,key", [
    ("жим лежа", "bench"), ("жиму леж", "bench"), ("жима лежё", "bench"), ("ЖИМ ЛЁЖА", "bench"),
    ("жим лежа наклон", "incline_bench"), ("жим лежа под наклоном 30 градусов", "incline_bench"),
    ("жим под наклоном 30°", "incline_bench"), ("наклоный жим", "incline_bench"),
    ("становая", "deadlift"), ("становая тяга", "deadlift"), ("мертвая тяга", "stiff_deadlift"),
    ("румынка", "romanian"), ("присед", "squat"), ("приседания со штангой", "squat"),
    ("бицепс гантели", "curl_db"), ("бицепс с гирей", "curl_kb"), ("бицепс ez", "curl_ez"),
    ("насосы", "side_bends"), ("шраги", "shrugs"), ("подтягивания", "pullups"), ("подтягиваня", "pullups"),
    ("брусья", "dips"), ("французский жим", "french"), ("французкий", "french"),
    ("икры сидя", "calf_seated"), ("икры стоя", "calf_standing"), ("выпады", "lunges"),
    ("тяга штанги в наклоне", "bent_row"), ("подъем коленей", "knee_raise"),
    ("скручивания на наклонной скамье", "crunch_incline"), ("скручивания горизонт", "crunch_flat"),
    ("жим штанги сидя", "ohp_seated"), ("гипер", "hyperext"), ("гиперэкст", "hyperext"),
    ("гиперэкстензия", "hyperext"), ("гиперэкстензии", "hyperext"), ("гиперэкстенция", "hyperext"),
    ("гипер экстензия", "hyperext"), ("гиперэкстензия с блином", "hyperext"), ("разводки стоя", "lat_raise"), ("скамья смитта бицепс", "curl_scott"),
])
def test_match(query, key):
    r = m.match(query, prefer=TEMPLATES["A"]["exercises"] + TEMPLATES["B"]["exercises"])
    assert r.status == "ok", (query, r)
    assert r.key == key


def test_ambiguous_asks():
    r = m.match("бицепс")
    assert r.status == "ask" and len(r.options) >= 2


def test_unknown():
    assert m.match("плавание брасом").status == "none"


def test_learn():
    mm = Matcher({"моя жимовуха": "bench"})
    assert mm.match("моя жимовуха").key == "bench"


@pytest.mark.parametrize("text,name,nums", [
    ("жим лежа 4х10х60", "жим лежа", "4х10х60"),
    ("Жим лёжа 4 x 10 x 57,5", "жим лежа", "4х10х57.5"),
    ("жим лежа под наклоном 30° 3х8х50", "жим лежа под наклоном", "3х8х50"),
    ("подтягивания 3х8 + 16", "подтягивания", "3х8 +16"),
    ("жим 60кг 10 10 8", "жим", "60 10 10 8"),
])
def test_split(text, name, nums):
    assert split_name(text) == (name, nums)


def sets(s, bw=False):
    return [(r.reps, r.weight, r.extra) for r in parse_sets(s, bw)]


def test_parse_formats():
    assert sets("4х10х60") == [(10, 60, 0)] * 4
    assert sets("4х10 60") == [(10, 60, 0)] * 4
    assert sets("4х10х57.5") == [(10, 57.5, 0)] * 4
    assert sets("60 10 10 8") == [(10, 60, 0), (10, 60, 0), (8, 60, 0)]
    assert sets("3х12", bw=True) == [(12, 0, 0)] * 3
    assert sets("3х8 +16", bw=True) == [(8, 0, 16)] * 3
    assert sets("3х8х16", bw=True) == [(8, 0, 16)] * 3
    assert sets("12 10 8", bw=True) == [(12, 0, 0), (10, 0, 0), (8, 0, 0)]
    assert sets("1 10", bw=True) == [(10, 0, 0)]          # подтягивания: 1 подход на 10 раз
    assert sets("3 12", bw=True) == [(12, 0, 0)] * 3
    assert sets("1 10 +16", bw=True) == [(10, 0, 16)]
    assert sets("12 10", bw=True) == [(12, 0, 0), (10, 0, 0)]   # первое число > 10 — это повторы
    # короткий формат «подходы вес повторы»
    assert sets("1 75 10") == [(10, 75, 0)]
    assert sets("3 80 8") == [(8, 80, 0)] * 3
    assert sets("3 57.5 10") == [(10, 57.5, 0)] * 3
    assert sets("3 20 15", bw=True) == [(15, 0, 20)] * 3   # гипер с блином 20
    assert sets("3 0 12", bw=True) == [(12, 0, 0)] * 3
    assert sets("60 10 10 8") == [(10, 60, 0), (10, 60, 0), (8, 60, 0)]
    assert sets("2х110", bw=True) == [(110, 0, 0)] * 2
    assert sets("3х15х20", bw=True) == [(15, 0, 20)] * 3   # гиперэкстензия с блином 20 кг


@pytest.mark.parametrize("bad", ["", "abc", "60", "50х10х60", "4х0х60", "4х10х900", "3 900 8"])
def test_parse_errors(bad):
    with pytest.raises(ParseError):
        parse_sets(bad)


def test_triset():
    kw, rounds, reps = ABS_TRISET["keywords"], ABS_TRISET["rounds"], [r for _, r in ABS_TRISET["items"]]
    assert parse_triset("пресс", kw, rounds, reps) == (2, [30, 110, 105])
    assert parse_triset("трисет 3", kw, rounds, reps) == (3, [30, 110, 105])
    assert parse_triset("пресс 2 30 110 100", kw, rounds, reps) == (2, [30, 110, 100])
    assert parse_triset("жим лежа 4х10х60", kw, rounds, reps) is None
    assert parse_triset("пресс 2х30", kw, rounds, reps) is None


def test_plates():
    combo, real = plates_per_side(60)            # гриф 7 кг -> 26,5 на сторону
    assert real == 60 and sum(combo) == 26.5
    combo, real = plates_per_side(87.5)          # 40,25 на сторону
    assert real == 87.5 and combo == [15, 13, 10, 1.25, 0.5, 0.5]
    combo, real = plates_per_side(33)            # 13 на сторону — жёлтые блины
    assert combo == [13]
    assert plates_per_side(1000)[1] == max_weight(7) == 150   # больше, чем есть блинов
    assert plates_per_side(30, bar=5)[1] == 30   # EZ-гриф 5 кг


def test_dumbbell_pair():
    combo, real = plates_per_side(11, bar=1, sides=4)
    assert combo == [5] and real == 11
    combo, real = plates_per_side(10, bar=1, sides=4)   # ровно 10 не собрать
    assert real == 9.5
    assert max_weight(1, sides=4) == 24.5


def test_db_and_reports(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    w1 = db.start_workout(1, "A", date(2026, 11, 2))
    db.add_entry(w1, "bench", parse_sets("4х10х57.5"))
    db.finish_workout(w1)
    w2 = db.start_workout(1, "A", date(2026, 11, 7))
    e = db.add_entry(w2, "bench", parse_sets("4х10х60"))
    db.add_entry(w2, "pullups", parse_sets("3х8 +16", True))
    rep = reports.workout_report(db, w2)
    assert "07.11.2026" in rep and "Жим лёжа — 4×10×60 кг (тоннаж 2400 кг)" in rep
    assert "Подтягивания — 3×8 +16 кг" in rep
    assert "02.11.2026" in reports.last_result_text(db, 1, "bench", w2)
    month = reports.period_report(db, 1, "month", today=date(2026, 11, 10))
    assert "Тренировок: 2 (А: 2, Б: 0)" in month and "Средний интервал: 5.0" in month
    ex = reports.exercise_report(db, 1, "bench")
    assert "Рекорд веса: 60 кг × 10" in ex
    assert db.delete_entry(e) and "Жим лёжа" not in reports.workout_report(db, w2)
    assert db.active_workout(1)["id"] == w2
    assert db.active_workout(2) is None  # данные разных пользователей не смешиваются


def test_all_template_keys_exist():
    for t in TEMPLATES.values():
        for k in t["exercises"]:
            assert k in BY_KEY


def test_generic_words_not_learned():
    assert m.is_generic("бицепс") and m.is_generic("тяга")
    assert not m.is_generic("мой жимчик")


@pytest.mark.parametrize("text,expected", [
    ("суп тарелка, 2 яблока средних, 1 банан, стакан молока, каши гречневой тарелку 150-200 грамм",
     [("soup", 300), ("apple", 300), ("banana", 120), ("milk", 200), ("buckwheat", 175)]),
    ("съел тарелку супа и 2 яблока", [("soup", 300), ("apple", 300)]),
    ("творог 200г", [("cottage5", 200)]),
    ("полтарелки борща", [("borsch", 150)]),
    ("3 яйца", [("egg", 165)]),
    ("гречка 200", [("buckwheat", 200)]),
    ("большое яблоко", [("apple", 195)]),
    ("хлеб 2 куска", [("bread_white", 60)]),
    ("вода 500", [("water", 500)]),
    ("выпил воды 2 стакана", [("water", 400)]),
    ("вода 1,5 л", [("water", 1500)]),
])
def test_food_parse(text, expected):
    items = nut.parse_food_text(text)
    assert [(i.key, i.grams) for i in items] == expected


def test_food_vs_exercise():
    assert nut.looks_like_food("2 яблока, банан")
    assert nut.looks_like_food("вода 500")
    assert not nut.looks_like_food("жим лежа 4х10х60")
    assert not nut.looks_like_food("пресс")
    assert not nut.looks_like_food("подтягивания 3х8 +16")


def test_targets():
    p = nut.parse_profile("профиль 85 180 м 50")
    assert p == dict(weight=85, height=180, sex="m", age=50, goal="maintain")
    t = nut.targets(p)
    # Миффлин: 10*85 + 6.25*180 - 5*50 + 5 = 1730; * 1.375 = 2378.75
    assert round(t["bmr"]) == 1730 and round(t["kcal"]) == 2379
    assert t["protein"] == 136 and t["fat"] == 85 and t["water"] == 2000
    assert nut.parse_profile("профиль 70 165 ж 45 похудение")["goal"] == "loss"
    with pytest.raises(ValueError):
        nut.parse_profile("профиль 85")


def test_nutrition_db(tmp_path):
    db = DB(str(tmp_path / "n.db"))
    db.save_profile(1, 85, 180, "m", 50, "maintain")
    for it in nut.parse_food_text("тарелка супа, 2 яблока, творог 200"):
        db.add_food(1, it.key, it.grams, nut.nutrients(it.key, it.grams))
    db.add_water(1, 500)
    rep = nut.day_report(db.food_day(1, date.today()), db.water_day(1, date.today()), db.get_profile(1), "Сегодня")
    assert "Творог 5% — 200 г · 242 ккал · Б 34" in rep
    assert "Вода: 500 / 2000 мл" in rep and "не хватает" in rep
    days = db.nutrition_days(1, date.today(), date.today())
    assert len(days) == 1 and days[0]["water"] == 500


def test_rest_and_duration():
    from exercises import rest_for
    from timer import mmss, parse_duration
    assert rest_for("squat") == 180 and rest_for("deadlift") == 180
    assert rest_for("bench") == 120 and rest_for("curl_db") == 90 and rest_for(None) == 90
    assert [parse_duration(x) for x in ["90", "2:30", "3", "1,5", "2 мин", "abc"]] == [90, 150, 180, 90, 120, None]
    assert mmss(125) == "2:05"


def test_sleep_parse():
    from summary import parse_sleep
    assert [parse_sleep(x) for x in ["7", "7,5", "7:30", "23:30-7:00", "23-7", "7 ч 30 мин", "0", "abc"]] == \
        [7, 7.5, 7.5, 7.5, 8, 7.5, None, None]


def test_day_and_week_summary(tmp_path):
    from datetime import datetime
    import summary
    db = DB(str(tmp_path / "s.db"))
    db.save_profile(1, 85, 180, "m", 50, "maintain")
    day = date(2026, 11, 7)
    w = db.start_workout(1, "A", day)
    db.add_entry(w, "bench", parse_sets("3 80 8"))
    for it in nut.parse_food_text("творог 200, гречка 200, курица 300, 2 банана, стакан молока, хлеб 3 куска"):
        db.add_food(1, it.key, it.grams, nut.nutrients(it.key, it.grams), day=day)
    db.add_water(1, 2000, day=day)
    db.set_sleep(1, 5, day=day)
    txt = summary.day_summary(db, 1, day, now=datetime(2026, 11, 7, 22))
    assert "тоннаж 1920 кг" in txt and "Сон 5 ч" in txt
    assert "вода" in txt.split("✅ В норме:")[1].split("\n")[0]
    assert "сон −2 ч" in txt and "Мало сна" in txt
    wk = summary.week_summary(db, 1, day)
    assert "Тренировок: 1" in wk and "Сон в среднем 5 ч" in wk


def test_glass_is_200():
    assert nut.parse_food_text("стакан воды")[0].grams == 200
    assert nut.parse_food_text("вода")[0].grams == 200
    assert nut.parse_food_text("стакан кефира")[0].grams == 200
