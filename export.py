"""Выгрузка всего дневника в Excel."""
from datetime import date
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font

from db import DB
from exercises import BY_KEY, GROUPS


def export_xlsx(db: DB, user_id: int) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Подходы"
    ws.append(["Дата", "Тренировка", "Упражнение", "Группа", "Подход", "Повторы",
               "Вес, кг", "Отягощение, кг", "Тоннаж, кг"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in db.all_sets(user_id):
        ex = BY_KEY.get(r["exercise_key"])
        ws.append([
            date.fromisoformat(r["day"]), "А" if r["wtype"] == "A" else "Б",
            ex.name if ex else r["exercise_key"], GROUPS.get(ex.group, "") if ex else "",
            r["set_no"], r["reps"], r["weight"], r["extra"], r["reps"] * (r["weight"] + r["extra"]),
        ])
    for row in ws.iter_rows(min_row=2, max_col=1):
        row[0].number_format = "DD.MM.YYYY"
    for col, width in zip("ABCDEFGHI", (12, 11, 40, 10, 8, 9, 9, 14, 11)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
