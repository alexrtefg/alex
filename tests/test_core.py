import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import DB  # noqa: E402
from exercises import ABS_TRISET, BY_KEY, TEMPLATES  # noqa: E402
from matcher import Matcher  # noqa: E402
from parser import ParseError, parse_sets, parse_triset, split_name  # noqa: E402
from plates import plates_per_side  # noqa: E402
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
    ("жим штанги сидя", "ohp_seated"), ("разводки стоя", "lat_raise"), ("скамья смитта бицепс", "curl_scott"),
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
    assert sets("2х110", bw=True) == [(110, 0, 0)] * 2


@pytest.mark.parametrize("bad", ["", "abc", "60", "50х10х60", "4х0х60", "4х10х900"])
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
    combo, real = plates_per_side(60)            # (60-10)/2 = 25 на сторону
    assert real == 60 and sum(combo) == 25 and len(combo) == 2
    combo, real = plates_per_side(36)            # 13 на сторону — жёлтые блины
    assert combo == [13]
    combo, real = plates_per_side(61)            # 25.5 на сторону
    assert real == 61
    combo, real = plates_per_side(71.3)          # не собрать ровно -> ближайший меньший
    assert real <= 71.3 and real > 70
    combo, real = plates_per_side(1000)          # больше, чем есть блинов
    assert real == 149.5
    assert plates_per_side(30, bar=5)[1] == 30   # EZ-гриф 5 кг


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
