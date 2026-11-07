"""Таймер отдыха между подходами: отсчёт в сообщении и сигнал в конце."""
import asyncio
import logging
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder

DEFAULT_SIGNAL = "Пошёл заниматься, герой! 💪"
UPDATE_EVERY = 15  # как часто обновлять оставшееся время в сообщении, секунды


def mmss(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    return f"{s // 60}:{s % 60:02d}"


def parse_duration(text: str) -> int | None:
    """«90» -> 90 с, «2:30» -> 150 с, «3» -> 3 мин (числа до 5 считаем минутами)."""
    t = text.strip().replace(",", ".").replace(" ", "")
    for suffix in ("мин", "м"):
        if t.endswith(suffix):
            try:
                return int(float(t[:-len(suffix)]) * 60)
            except ValueError:
                return None
    if ":" in t:
        m, _, s = t.partition(":")
        if m.isdigit() and s.isdigit():
            return int(m) * 60 + int(s)
        return None
    try:
        v = float(t)
    except ValueError:
        return None
    return int(v * 60) if v <= 5 else int(v)


def timer_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="−30 с", callback_data="rt:-30")
    kb.button(text="+30 с", callback_data="rt:30")
    kb.button(text="⏹ Стоп", callback_data="rt:stop")
    return kb.as_markup()


@dataclass
class RestTimer:
    chat_id: int
    message_id: int
    label: str
    end: float
    task: asyncio.Task | None = field(default=None, repr=False)


class Timers:
    def __init__(self, get_signal):
        """get_signal(user_id) -> (text, ("voice" | "audio", file_id) | None)"""
        self.active: dict[int, RestTimer] = {}
        self.get_signal = get_signal

    @staticmethod
    def _text(label: str, left: float) -> str:
        return f"⏱ Отдых: осталось {mmss(left)}\n{label}"

    async def start(self, bot: Bot, user_id: int, chat_id: int, seconds: int, label: str):
        self.cancel(user_id)
        loop = asyncio.get_running_loop()
        msg = await bot.send_message(chat_id, self._text(label, seconds), reply_markup=timer_kb())
        t = RestTimer(chat_id, msg.message_id, label, loop.time() + seconds)
        t.task = asyncio.create_task(self._run(bot, user_id, t))
        self.active[user_id] = t

    def cancel(self, user_id: int) -> RestTimer | None:
        t = self.active.pop(user_id, None)
        if t and t.task and t.task is not asyncio.current_task():
            t.task.cancel()
        return t

    def adjust(self, user_id: int, delta: int) -> float | None:
        t = self.active.get(user_id)
        if not t:
            return None
        loop = asyncio.get_running_loop()
        t.end = max(loop.time() + 5, t.end + delta)
        return t.end - loop.time()

    async def _run(self, bot: Bot, user_id: int, t: RestTimer):
        loop = asyncio.get_running_loop()
        shown = None
        try:
            while True:
                left = t.end - loop.time()
                if left <= 0:
                    break
                bucket = int(left // UPDATE_EVERY)
                if shown is not None and bucket != shown:
                    await self._edit(bot, t, self._text(t.label, left), timer_kb())
                shown = bucket
                await asyncio.sleep(min(1.0, left))
            await self._edit(bot, t, f"✅ Отдых окончен\n{t.label}", None)
            text, media = self.get_signal(user_id)
            await bot.send_message(t.chat_id, f"🔔 {text}\n{t.label}")   # новое сообщение = звук уведомления
            if media:  # свой голосовой сигнал: ("voice" | "audio", file_id)
                kind, file_id = media
                if kind == "audio":
                    await bot.send_audio(t.chat_id, file_id)
                else:
                    await bot.send_voice(t.chat_id, file_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Ошибка таймера отдыха")
        finally:
            if self.active.get(user_id) is t:
                self.active.pop(user_id, None)

    @staticmethod
    async def _edit(bot: Bot, t: RestTimer, text: str, markup):
        try:
            await bot.edit_message_text(text, chat_id=t.chat_id, message_id=t.message_id, reply_markup=markup)
        except Exception:
            pass  # сообщение могли удалить или текст не изменился — не страшно
