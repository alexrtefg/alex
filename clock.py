"""«Сегодня» для дневника: день заканчивается в 4 утра, а не в полночь.

Тренировка, начатая в 23:30, продолжается после полуночи; ужин в 00:30 и вода в 01:00 идут в «вчерашний» вечер.
"""
from datetime import date, datetime, timedelta

DAY_START_HOUR = 4


def today() -> date:
    return (datetime.now() - timedelta(hours=DAY_START_HOUR)).date()
