import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import DB  # noqa: E402
import clock  # noqa: E402
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
    assert "07.11.2026" in rep and "Жим лёжа — 4×10 по 60 кг (тоннаж 2400 кг)" in rep
    assert "Подтягивания — 3×8 +16 кг" in rep
    assert "02.11.2026" in reports.last_result_text(db, 1, "bench", w2)
    month = reports.period_report(db, 1, "month", today=date(2026, 11, 10))
    assert "Тренировок: 2 (А: 2, Б: 0)" in month and "Средний интервал: 5 дн." in month
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
    rep = nut.day_report(db.food_day(1, clock.today()), db.water_day(1, clock.today()), db.get_profile(1), "Сегодня")
    assert "Творог 5% — 200 г · 242 ккал · Б 34" in rep
    assert "Вода: 500 / 2000 мл" in rep and "не хватает" in rep
    days = db.nutrition_days(1, clock.today(), clock.today())
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


@pytest.mark.parametrize("text,expected", [
    ("скумбрия с картошкой", [("mackerel", 150), ("potato_boiled", 200)]),
    ("рыба", [("fish", 150)]),
    ("цикорий", [("chicory", 200)]),
    ("кофе с молоком", [("coffee_milk", 250)]),
    ("кофе с сахаром", [("coffee_sugar", 200)]),
    ("греча", [("buckwheat", 200)]),
    ("рисовая каша", [("rice_porridge", 250)]),
    ("манная каша", [("semolina", 250)]),
    ("овсяная каша", [("oatmeal_water", 250)]),
    ("геркулес", [("oatmeal_water", 250)]),
    ("каша с молоком", [("porridge_milk", 250)]),
    ("бутерброд с маслом и сыром", [("sandw_butter_cheese", 60)]),
    ("2 бутерброда с маслом и сыром", [("sandw_butter_cheese", 120)]),
    ("бутерброд с копченой колбасой", [("sandw_smoked", 55)]),
    ("бутерброд с колбасой", [("sandw_sausage", 60)]),
    ("стакан гейнера", [("gainer", 60)]),
    ("протеиновый коктейль", [("protein", 30)]),
    ("всаа стакан", [("bcaa", 7)]),
    ("масса творожная", [("curd_mass", 100)]),
    ("творог 18", [("cottage18", 200)]),
    ("творог 9 200", [("cottage9", 200)]),
    ("творог 200", [("cottage5", 200)]),
    ("курица с рисом", [("chicken_breast", 150), ("rice", 200)]),
])
def test_food_more(text, expected):
    assert [(i.key, i.grams) for i in nut.parse_food_text(text)] == expected


def test_profile_without_word():
    assert nut.looks_like_profile("76 175 м 50")
    assert nut.parse_profile("76 175 м 50") == dict(weight=76, height=175, sex="m", age=50, goal="maintain")
    assert nut.looks_like_profile("профиль 76 175 м 50")
    assert not nut.looks_like_profile("3 80 8") and not nut.looks_like_profile("2 яблока")


def test_body_weight_tonnage_effort_interval(tmp_path):
    from exercises import BY_KEY as EX
    db = DB(str(tmp_path / "b.db"))
    db.save_profile(1, 76, 175, "m", 50, "maintain")
    w1 = db.start_workout(1, "A", date(2026, 11, 8))
    e = db.add_entry(w1, "pullups", parse_sets("3 10", True), body=76 * EX["pullups"].bw_factor)
    db.add_entry(w1, "hyperext", parse_sets("3 20 15", True), body=76 * EX["hyperext"].bw_factor)
    db.set_effort(e, 1)
    db.set_wellbeing(w1, 4)
    db.add_pain(w1, "плечо")
    rep = reports.workout_report(db, w1)
    assert "Подтягивания — 3×10 (свой вес) (тоннаж 2280 кг) 😌" in rep        # 3 * 10 * 76
    assert "Гиперэкстензия — 3×15 +20 кг (тоннаж 2610 кг)" in rep             # 3 * 15 * (20 + 38)
    assert "Самочувствие: 4/5" in rep and "Болит: плечо" in rep
    db.start_workout(1, "B", date(2026, 11, 8))                                # вторая запись в тот же день
    month = reports.period_report(db, 1, "month", today=date(2026, 11, 10))
    assert "Средний интервал" not in month                                     # один день — интервала нет


def test_backfill_body(tmp_path):
    db = DB(str(tmp_path / "f.db"))
    db.save_profile(1, 80, 180, "m", 50, "maintain")
    w = db.start_workout(1, "A", date(2026, 11, 1))
    db.add_entry(w, "pullups", parse_sets("2 10", True))   # старая запись без веса тела
    db.backfill_body({"pullups": 1.0})
    assert reports.tonnage(db.workout_sets(w)) == 1600


def test_measures(tmp_path):
    import measures
    assert measures.parse_measures("талия 88 грудь 102 бицепс 38 бедро 58 шея 40") == \
        {"waist": 88, "chest": 102, "biceps": 38, "thigh": 58, "neck": 40}
    assert measures.looks_like_measures("бедро 58") and not measures.looks_like_measures("бедро 150")
    assert not measures.looks_like_measures("курица 150")
    db = DB(str(tmp_path / "m.db"))
    db.add_measure(1, "waist", 90, date(2026, 10, 1))
    db.add_measure(1, "waist", 87.5, date(2026, 11, 1))
    assert "Талия: 87,5 (было 90 на 01.10.2026, −2,5)" in measures.history_text(db, 1)


def test_planner_rules(tmp_path):
    import planner
    from library import LIB, PATTERN
    db = DB(str(tmp_path / "p.db"))
    db.save_profile(1, 76, 175, "m", 50, "maintain")
    day = date(2026, 11, 8)
    for seed in range(6):
        p = planner.build_plan(db, 1, ["chest", "back", "legs", "shoulders", "biceps", "triceps", "abs"],
                               seed=seed, today=day)
        keys = p.keys
        assert len(keys) == len(set(keys))                                  # без повторов
        assert sum(LIB[k].spine for k in keys) <= 2                         # поясница: не больше 2
        assert len([k for k in keys if LIB[k].kind != "A"]) <= 7            # всё тело — до 7 упражнений + пресс
        assert keys[-1] in [k for k in LIB if LIB[k].kind == "A"]           # пресс в конце
    p = planner.build_plan(db, 1, ["chest"], today=day)
    assert LIB[p.keys[0]].kind == "C"                                       # первым — базовое
    pats = [PATTERN.get(k) for k in p.keys]
    assert len(set(pats)) >= 2                                              # не одно и то же движение
    # болело плечо -> упражнения на плечо убраны
    w = db.start_workout(1, "A", date(2026, 11, 5))
    db.add_pain(w, "болит плечо")
    p = planner.build_plan(db, 1, ["chest", "shoulders"], today=day)
    assert all("shoulder" not in LIB[k].joints for k in p.keys)
    assert any("плечо" in n for n in p.notes)
    # мало сна -> подходов меньше
    db.set_sleep(1, 5, day=day)
    p2 = planner.build_plan(db, 1, ["back"], today=day)
    assert all(i.sets <= LIB[i.key].sets - 1 or i.sets == 2 for i in p2.items)


def test_usability_fixes():
    assert reports.human_sets([dict(reps=12, weight=16, extra=0)] * 3) == "3 подхода × 12 раз × 16 кг"
    assert reports.human_sets([dict(reps=1, weight=75, extra=0)]) == "1 подход × 1 раз × 75 кг"
    assert reports.fmt_sets([dict(reps=10, weight=60, extra=0)] * 4) == "4×10 по 60 кг"
    assert [(i.key, i.status) for i in nut.parse_food_text("хинкали 5 штук")] == [(None, "none")]   # не угадываем
    assert nut.looks_food_shaped("хинкали 5 штук") and nut.looks_food_shaped("гречка 200, хинкали 5")
    assert not nut.looks_food_shaped("жим 3х8х80") and not nut.looks_food_shaped("привет")
    assert [(i.key, i.grams) for i in nut.parse_food_text("протеин на молоке")] == [("protein", 30), ("milk", 250)]


def test_day_ends_at_4am(monkeypatch):
    from datetime import datetime as real_dt
    class Fake(real_dt):
        @classmethod
        def now(cls, tz=None):
            return real_dt(2026, 11, 9, 1, 30)
    monkeypatch.setattr(clock, "datetime", Fake)
    assert clock.today() == date(2026, 11, 8)       # 01:30 ночи — ещё «вчерашний» день


def test_custom_food_and_edit(tmp_path):
    d = nut.parse_custom_food("добавь продукт хинкали 235 10 12 22 шт 80")
    assert d == dict(name="хинкали", kcal=235, protein=10, fat=12, carbs=22, piece=80, portion=None)
    d2 = nut.parse_custom_food("добавь продукт сырники домашние ккал 220 б 15 ж 10 у 18 шт 60")
    assert d2["name"] == "сырники домашние" and d2["protein"] == 15 and d2["piece"] == 60
    with pytest.raises(ValueError):
        nut.parse_custom_food("добавь продукт хинкали")
    db = DB(str(tmp_path / "c.db"))
    fid = db.add_custom_food(1, **d)
    nut.register_custom(db.custom_foods(1)[0])
    assert [(i.key, i.grams) for i in nut.parse_food_text("хинкали 5 штук")] == [(f"my{fid}", 400)]
    nut.unregister_custom(fid)
    assert nut.parse_food_text("хинкали 5 штук")[0].status == "none"
    # правка старой записи
    w = db.start_workout(1, "B", date(2026, 11, 2))
    e = db.add_entry(w, "squat", parse_sets("3 80 6"))
    db.replace_entry_sets(e, parse_sets("3 85 6"))
    assert reports.tonnage(db.workout_sets(w)) == 3 * 6 * 85
    db.set_entry_exercise(e, "front_squat", 0)
    assert db.workout_sets(w)[0]["exercise_key"] == "front_squat"
    db.delete_workout(w)
    assert db.get_workout(w) is None
