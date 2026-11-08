"""Хранение дневника в SQLite (один файл gym.db)."""
import sqlite3
from datetime import date, datetime

from parser import SetRow

SCHEMA = """
CREATE TABLE IF NOT EXISTS workouts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    day         TEXT NOT NULL,          -- дата тренировки, ГГГГ-ММ-ДД
    wtype       TEXT NOT NULL,          -- A или B
    started_at  TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS entries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    workout_id   INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
    exercise_key TEXT NOT NULL,
    raw_text     TEXT,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sets (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    set_no   INTEGER NOT NULL,
    reps     INTEGER NOT NULL,
    weight   REAL NOT NULL DEFAULT 0,
    extra    REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS aliases (
    alias        TEXT PRIMARY KEY,
    exercise_key TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profile (
    user_id         INTEGER PRIMARY KEY,
    weight          REAL NOT NULL,
    height          REAL NOT NULL,
    sex             TEXT NOT NULL,      -- m / f
    age             INTEGER NOT NULL,
    goal            TEXT NOT NULL DEFAULT 'maintain',
    water_goal      INTEGER NOT NULL DEFAULT 2000,
    water_reminders INTEGER NOT NULL DEFAULT 1,
    last_reminder   TEXT
);
CREATE TABLE IF NOT EXISTS weight_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    day     TEXT NOT NULL,
    weight  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS food_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    day        TEXT NOT NULL,
    food_key   TEXT NOT NULL,
    grams      REAL NOT NULL,
    kcal       REAL NOT NULL,
    protein    REAL NOT NULL,
    fat        REAL NOT NULL,
    carbs      REAL NOT NULL,
    raw_text   TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS water_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    day        TEXT NOT NULL,
    ml         REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_food_user_day ON food_log(user_id, day);
CREATE INDEX IF NOT EXISTS idx_water_user_day ON water_log(user_id, day);
CREATE TABLE IF NOT EXISTS sleep_log (
    user_id INTEGER NOT NULL,
    day     TEXT NOT NULL,          -- день, в который проснулся
    hours   REAL NOT NULL,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS measurements (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    day     TEXT NOT NULL,
    part    TEXT NOT NULL,          -- waist, chest, biceps, thigh, neck
    value   REAL NOT NULL           -- см
);
CREATE TABLE IF NOT EXISTS settings (
    user_id INTEGER NOT NULL,
    key     TEXT NOT NULL,
    value   TEXT,
    PRIMARY KEY (user_id, key)
);
CREATE INDEX IF NOT EXISTS idx_workouts_user_day ON workouts(user_id, day);
CREATE INDEX IF NOT EXISTS idx_entries_workout ON entries(workout_id);
CREATE INDEX IF NOT EXISTS idx_sets_entry ON sets(entry_id);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str = "gym.db"):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        # Новые колонки для баз, созданных старой версией бота (данные сохраняются).
        self._add_column("sets", "body", "REAL NOT NULL DEFAULT 0")      # вес тела в подходе (подтягивания и т.п.)
        self._add_column("entries", "effort", "INTEGER")                 # 1 легко, 2 норм, 3 тяжело
        self._add_column("workouts", "wellbeing", "INTEGER")             # самочувствие 1–5
        self._add_column("workouts", "pain", "TEXT")                     # что болит
        self.conn.commit()

    def _add_column(self, table: str, column: str, decl: str):
        cols = [r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")]
        if column not in cols:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    # --- тренировки ---
    def active_workout(self, user_id: int):
        return self.conn.execute(
            "SELECT * FROM workouts WHERE user_id=? AND finished_at IS NULL ORDER BY id DESC LIMIT 1",
            (user_id,)).fetchone()

    def last_workout(self, user_id: int, wtype: str | None = None, exclude_id: int | None = None):
        sql = "SELECT * FROM workouts WHERE user_id=?"
        args: list = [user_id]
        if wtype:
            sql += " AND wtype=?"
            args.append(wtype)
        if exclude_id:
            sql += " AND id<>?"
            args.append(exclude_id)
        sql += " ORDER BY day DESC, id DESC LIMIT 1"
        return self.conn.execute(sql, args).fetchone()

    def start_workout(self, user_id: int, wtype: str, day: date | None = None) -> int:
        day = day or date.today()
        cur = self.conn.execute(
            "INSERT INTO workouts(user_id, day, wtype, started_at) VALUES (?,?,?,?)",
            (user_id, day.isoformat(), wtype, now()))
        self.conn.commit()
        return cur.lastrowid

    def finish_workout(self, workout_id: int):
        self.conn.execute("UPDATE workouts SET finished_at=? WHERE id=?", (now(), workout_id))
        self.conn.commit()

    def delete_workout_if_empty(self, workout_id: int) -> bool:
        n = self.conn.execute("SELECT COUNT(*) FROM entries WHERE workout_id=?", (workout_id,)).fetchone()[0]
        if n == 0:
            self.conn.execute("DELETE FROM workouts WHERE id=?", (workout_id,))
            self.conn.commit()
            return True
        return False

    def get_workout(self, workout_id: int):
        return self.conn.execute("SELECT * FROM workouts WHERE id=?", (workout_id,)).fetchone()

    def workouts_between(self, user_id: int, start: date, end: date):
        return self.conn.execute(
            "SELECT * FROM workouts WHERE user_id=? AND day BETWEEN ? AND ? ORDER BY day, id",
            (user_id, start.isoformat(), end.isoformat())).fetchall()

    def recent_workouts(self, user_id: int, limit: int = 10):
        return self.conn.execute(
            "SELECT * FROM workouts WHERE user_id=? ORDER BY day DESC, id DESC LIMIT ?",
            (user_id, limit)).fetchall()

    # --- записи ---
    def add_entry(self, workout_id: int, exercise_key: str, rows: list[SetRow], raw_text: str = "",
                  body: float = 0.0) -> int:
        cur = self.conn.execute(
            "INSERT INTO entries(workout_id, exercise_key, raw_text, created_at) VALUES (?,?,?,?)",
            (workout_id, exercise_key, raw_text, now()))
        entry_id = cur.lastrowid
        start_no = self.conn.execute(
            "SELECT COUNT(*) FROM sets s JOIN entries e ON e.id=s.entry_id "
            "WHERE e.workout_id=? AND e.exercise_key=?", (workout_id, exercise_key)).fetchone()[0]
        self.conn.executemany(
            "INSERT INTO sets(entry_id, set_no, reps, weight, extra, body) VALUES (?,?,?,?,?,?)",
            [(entry_id, start_no + i + 1, r.reps, r.weight, r.extra, body) for i, r in enumerate(rows)])
        self.conn.commit()
        return entry_id

    def delete_entry(self, entry_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM entries WHERE id=?", (entry_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def last_entry(self, workout_id: int):
        return self.conn.execute(
            "SELECT * FROM entries WHERE workout_id=? ORDER BY id DESC LIMIT 1", (workout_id,)).fetchone()

    def workout_sets(self, workout_id: int):
        """Все подходы тренировки: exercise_key, set_no, reps, weight, extra, entry_id."""
        return self.conn.execute(
            "SELECT e.exercise_key, e.id AS entry_id, e.effort, s.set_no, s.reps, s.weight, s.extra, s.body "
            "FROM entries e JOIN sets s ON s.entry_id=e.id WHERE e.workout_id=? "
            "ORDER BY e.id, s.set_no", (workout_id,)).fetchall()

    def exercise_history(self, user_id: int, exercise_key: str, exclude_workout: int | None = None):
        """Подходы упражнения по всем тренировкам, от старых к новым."""
        return self.conn.execute(
            "SELECT w.id AS workout_id, w.day, e.effort, s.reps, s.weight, s.extra, s.body "
            "FROM workouts w JOIN entries e ON e.workout_id=w.id JOIN sets s ON s.entry_id=e.id "
            "WHERE w.user_id=? AND e.exercise_key=? AND w.id<>? ORDER BY w.day, w.id, e.id, s.set_no",
            (user_id, exercise_key, exclude_workout or -1)).fetchall()

    def all_sets(self, user_id: int):
        return self.conn.execute(
            "SELECT w.day, w.wtype, e.exercise_key, e.effort, s.set_no, s.reps, s.weight, s.extra, s.body "
            "FROM workouts w JOIN entries e ON e.workout_id=w.id JOIN sets s ON s.entry_id=e.id "
            "WHERE w.user_id=? ORDER BY w.day, w.id, e.id, s.set_no", (user_id,)).fetchall()

    # --- выученные названия ---
    def learned_aliases(self) -> dict:
        return {r["alias"]: r["exercise_key"] for r in self.conn.execute("SELECT * FROM aliases")}

    def save_alias(self, alias: str, exercise_key: str):
        self.conn.execute("INSERT OR REPLACE INTO aliases(alias, exercise_key) VALUES (?,?)", (alias, exercise_key))
        self.conn.commit()

    # --- профиль ---
    def get_profile(self, user_id: int):
        return self.conn.execute("SELECT * FROM profile WHERE user_id=?", (user_id,)).fetchone()

    def save_profile(self, user_id: int, weight: float, height: float, sex: str, age: int, goal: str):
        self.conn.execute(
            "INSERT INTO profile(user_id, weight, height, sex, age, goal) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET weight=excluded.weight, height=excluded.height, "
            "sex=excluded.sex, age=excluded.age, goal=excluded.goal",
            (user_id, weight, height, sex, age, goal))
        self.log_weight(user_id, weight)

    def log_weight(self, user_id: int, weight: float, day: date | None = None):
        day = (day or date.today()).isoformat()
        self.conn.execute("DELETE FROM weight_log WHERE user_id=? AND day=?", (user_id, day))
        self.conn.execute("INSERT INTO weight_log(user_id, day, weight) VALUES (?,?,?)", (user_id, day, weight))
        self.conn.execute("UPDATE profile SET weight=? WHERE user_id=?", (weight, user_id))
        self.conn.commit()

    def weight_history(self, user_id: int):
        return self.conn.execute("SELECT day, weight FROM weight_log WHERE user_id=? ORDER BY day",
                                 (user_id,)).fetchall()

    def set_profile_field(self, user_id: int, field: str, value):
        assert field in ("water_goal", "water_reminders", "last_reminder", "goal")
        self.conn.execute(f"UPDATE profile SET {field}=? WHERE user_id=?", (value, user_id))
        self.conn.commit()

    def reminder_users(self) -> list[int]:
        """Кому слать напоминания о воде: все, кто пользуется ботом, кроме выключивших (/water_off)."""
        rows = self.conn.execute(
            "SELECT DISTINCT user_id FROM ("
            " SELECT user_id FROM settings WHERE key='known'"
            " UNION SELECT user_id FROM profile"
            " UNION SELECT user_id FROM water_log UNION SELECT user_id FROM food_log"
            " UNION SELECT user_id FROM workouts)").fetchall()
        return [r["user_id"] for r in rows if self.get_setting(r["user_id"], "water_rem", "1") == "1"]

    # --- еда и вода ---
    def add_food(self, user_id: int, food_key: str, grams: float, n: dict, raw: str = "",
                 day: date | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO food_log(user_id, day, food_key, grams, kcal, protein, fat, carbs, raw_text, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (user_id, (day or date.today()).isoformat(), food_key, grams, n["kcal"], n["protein"], n["fat"],
             n["carbs"], raw, now()))
        self.conn.commit()
        return cur.lastrowid

    def delete_food(self, user_id: int, food_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM food_log WHERE id=? AND user_id=?", (food_id, user_id))
        self.conn.commit()
        return cur.rowcount > 0

    def food_day(self, user_id: int, day: date):
        return self.conn.execute("SELECT * FROM food_log WHERE user_id=? AND day=? ORDER BY id",
                                 (user_id, day.isoformat())).fetchall()

    def add_water(self, user_id: int, ml: float, day: date | None = None) -> int:
        cur = self.conn.execute("INSERT INTO water_log(user_id, day, ml, created_at) VALUES (?,?,?,?)",
                                (user_id, (day or date.today()).isoformat(), ml, now()))
        self.conn.commit()
        return cur.lastrowid

    def delete_water(self, user_id: int, water_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM water_log WHERE id=? AND user_id=?", (water_id, user_id))
        self.conn.commit()
        return cur.rowcount > 0

    def water_day(self, user_id: int, day: date) -> float:
        return self.conn.execute("SELECT COALESCE(SUM(ml), 0) FROM water_log WHERE user_id=? AND day=?",
                                 (user_id, day.isoformat())).fetchone()[0]

    def nutrition_days(self, user_id: int, start: date, end: date):
        """Итоги по дням: day, kcal, protein, fat, carbs, water."""
        return self.conn.execute(
            "SELECT d.day, COALESCE(f.kcal,0) kcal, COALESCE(f.protein,0) protein, COALESCE(f.fat,0) fat, "
            "COALESCE(f.carbs,0) carbs, COALESCE(w.ml,0) water FROM "
            "(SELECT day FROM food_log WHERE user_id=:u AND day BETWEEN :s AND :e "
            " UNION SELECT day FROM water_log WHERE user_id=:u AND day BETWEEN :s AND :e) d "
            "LEFT JOIN (SELECT day, SUM(kcal) kcal, SUM(protein) protein, SUM(fat) fat, SUM(carbs) carbs "
            "           FROM food_log WHERE user_id=:u GROUP BY day) f ON f.day=d.day "
            "LEFT JOIN (SELECT day, SUM(ml) ml FROM water_log WHERE user_id=:u GROUP BY day) w ON w.day=d.day "
            "ORDER BY d.day", dict(u=user_id, s=start.isoformat(), e=end.isoformat())).fetchall()

    # --- настройки ---
    def get_setting(self, user_id: int, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE user_id=? AND key=?", (user_id, key)).fetchone()
        return row["value"] if row and row["value"] is not None else default

    def set_setting(self, user_id: int, key: str, value: str | None):
        self.conn.execute("INSERT OR REPLACE INTO settings(user_id, key, value) VALUES (?,?,?)", (user_id, key, value))
        self.conn.commit()

    # --- сон ---
    def set_sleep(self, user_id: int, hours: float, day: date | None = None):
        self.conn.execute("INSERT OR REPLACE INTO sleep_log(user_id, day, hours) VALUES (?,?,?)",
                          (user_id, (day or date.today()).isoformat(), hours))
        self.conn.commit()

    def get_sleep(self, user_id: int, day: date) -> float | None:
        row = self.conn.execute("SELECT hours FROM sleep_log WHERE user_id=? AND day=?",
                                (user_id, day.isoformat())).fetchone()
        return row["hours"] if row else None

    def sleep_between(self, user_id: int, start: date, end: date):
        return self.conn.execute("SELECT day, hours FROM sleep_log WHERE user_id=? AND day BETWEEN ? AND ? ORDER BY day",
                                 (user_id, start.isoformat(), end.isoformat())).fetchall()

    # --- тяжесть подхода, самочувствие, боль ---
    def set_effort(self, entry_id: int, effort: int):
        self.conn.execute("UPDATE entries SET effort=? WHERE id=?", (effort, entry_id))
        self.conn.commit()

    def get_entry(self, entry_id: int):
        return self.conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()

    def set_wellbeing(self, workout_id: int, value: int):
        self.conn.execute("UPDATE workouts SET wellbeing=? WHERE id=?", (value, workout_id))
        self.conn.commit()

    def add_pain(self, workout_id: int, text: str):
        w = self.get_workout(workout_id)
        pain = f"{w['pain']}; {text}" if w["pain"] else text
        self.conn.execute("UPDATE workouts SET pain=? WHERE id=?", (pain, workout_id))
        self.conn.commit()

    def pains_between(self, user_id: int, start: date, end: date):
        return self.conn.execute(
            "SELECT day, pain FROM workouts WHERE user_id=? AND day BETWEEN ? AND ? AND pain IS NOT NULL ORDER BY day",
            (user_id, start.isoformat(), end.isoformat())).fetchall()

    # --- вес тела на дату и подтягивания/брусья в тоннаже ---
    def body_weight_on(self, user_id: int, day: date | None = None) -> float | None:
        day = (day or date.today()).isoformat()
        row = self.conn.execute("SELECT weight FROM weight_log WHERE user_id=? AND day<=? ORDER BY day DESC LIMIT 1",
                                (user_id, day)).fetchone()
        if row:
            return row["weight"]
        p = self.get_profile(user_id)
        return p["weight"] if p else None

    def backfill_body(self, factors: dict):
        """Старые записи подтягиваний/брусьев без веса тела: дописать его по профилю."""
        for p in self.conn.execute("SELECT user_id FROM profile").fetchall():
            for w in self.conn.execute("SELECT id, day FROM workouts WHERE user_id=?", (p["user_id"],)).fetchall():
                bw = self.body_weight_on(p["user_id"], date.fromisoformat(w["day"]))
                for key, f in factors.items():
                    self.conn.execute(
                        "UPDATE sets SET body=? WHERE body=0 AND entry_id IN "
                        "(SELECT id FROM entries WHERE workout_id=? AND exercise_key=?)",
                        (round(bw * f, 1), w["id"], key))
        self.conn.commit()

    # --- замеры тела ---
    def add_measure(self, user_id: int, part: str, value: float, day: date | None = None):
        day = (day or date.today()).isoformat()
        self.conn.execute("DELETE FROM measurements WHERE user_id=? AND day=? AND part=?", (user_id, day, part))
        self.conn.execute("INSERT INTO measurements(user_id, day, part, value) VALUES (?,?,?,?)",
                          (user_id, day, part, value))
        self.conn.commit()

    def measure_history(self, user_id: int, part: str):
        return self.conn.execute("SELECT day, value FROM measurements WHERE user_id=? AND part=? ORDER BY day",
                                 (user_id, part)).fetchall()

    def last_measure_day(self, user_id: int) -> str | None:
        row = self.conn.execute("SELECT MAX(day) d FROM measurements WHERE user_id=?", (user_id,)).fetchone()
        return row["d"] if row else None
