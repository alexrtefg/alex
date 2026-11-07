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
        self.conn.commit()

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
    def add_entry(self, workout_id: int, exercise_key: str, rows: list[SetRow], raw_text: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO entries(workout_id, exercise_key, raw_text, created_at) VALUES (?,?,?,?)",
            (workout_id, exercise_key, raw_text, now()))
        entry_id = cur.lastrowid
        start_no = self.conn.execute(
            "SELECT COUNT(*) FROM sets s JOIN entries e ON e.id=s.entry_id "
            "WHERE e.workout_id=? AND e.exercise_key=?", (workout_id, exercise_key)).fetchone()[0]
        self.conn.executemany(
            "INSERT INTO sets(entry_id, set_no, reps, weight, extra) VALUES (?,?,?,?,?)",
            [(entry_id, start_no + i + 1, r.reps, r.weight, r.extra) for i, r in enumerate(rows)])
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
            "SELECT e.exercise_key, e.id AS entry_id, s.set_no, s.reps, s.weight, s.extra "
            "FROM entries e JOIN sets s ON s.entry_id=e.id WHERE e.workout_id=? "
            "ORDER BY e.id, s.set_no", (workout_id,)).fetchall()

    def exercise_history(self, user_id: int, exercise_key: str, exclude_workout: int | None = None):
        """Подходы упражнения по всем тренировкам, от старых к новым."""
        return self.conn.execute(
            "SELECT w.id AS workout_id, w.day, s.reps, s.weight, s.extra "
            "FROM workouts w JOIN entries e ON e.workout_id=w.id JOIN sets s ON s.entry_id=e.id "
            "WHERE w.user_id=? AND e.exercise_key=? AND w.id<>? ORDER BY w.day, w.id, e.id, s.set_no",
            (user_id, exercise_key, exclude_workout or -1)).fetchall()

    def all_sets(self, user_id: int):
        return self.conn.execute(
            "SELECT w.day, w.wtype, e.exercise_key, s.set_no, s.reps, s.weight, s.extra "
            "FROM workouts w JOIN entries e ON e.workout_id=w.id JOIN sets s ON s.entry_id=e.id "
            "WHERE w.user_id=? ORDER BY w.day, w.id, e.id, s.set_no", (user_id,)).fetchall()

    # --- выученные названия ---
    def learned_aliases(self) -> dict:
        return {r["alias"]: r["exercise_key"] for r in self.conn.execute("SELECT * FROM aliases")}

    def save_alias(self, alias: str, exercise_key: str):
        self.conn.execute("INSERT OR REPLACE INTO aliases(alias, exercise_key) VALUES (?,?)", (alias, exercise_key))
        self.conn.commit()
