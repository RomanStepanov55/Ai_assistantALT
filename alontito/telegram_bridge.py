import io
import os
import asyncio
from typing import Optional

from brain.opus_local_core import OpusLocalCore
from games.universal_explorer import UniversalExplorer

try:
    from aiogram import Bot, Dispatcher, types
    from aiogram.filters import Command
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
except ImportError:
    Bot = None


class AlontitoTelegramBridge:
    def __init__(self, token: str, allowed_user_id: int, brain: OpusLocalCore, explorer: UniversalExplorer):
        self.token = token
        self.allowed_user_id = allowed_user_id
        self.brain = brain
        self.explorer = explorer

    def _is_authorized(self, user_id: int) -> bool:
        return self.allowed_user_id == 0 or user_id == self.allowed_user_id

    async def start_polling(self) -> None:
        if Bot is None or not self.token:
            return
        bot = Bot(token=self.token)
        dp = Dispatcher()

        @dp.message(Command("screen"))
        async def cmd_screen(msg: types.Message):
            if not self._is_authorized(msg.from_user.id):
                return
            obs = await asyncio.to_thread(self.explorer.eye.observe_verified, 0.15)
            buf = io.BytesIO()
            obs["som_image"].save(buf, format="JPEG", quality=80)
            photo = BufferedInputFile(buf.getvalue(), filename="som_screen.jpg")
            await msg.answer_photo(photo, caption=f"Метрики HUD: {obs['metrics']}")

        @dp.message(Command("explore"))
        async def cmd_explore(msg: types.Message):
            if not self._is_authorized(msg.from_user.id):
                return
            self.explorer.start_background()
            await msg.answer("🚀 Автономный исследователь UCB1 запущен.")

        @dp.message(Command("stop"))
        async def cmd_stop(msg: types.Message):
            if not self._is_authorized(msg.from_user.id):
                return
            self.explorer.stop()
            await msg.answer("⏹ Исследователь UCB1 остановлен.")

        @dp.callback_query()
        async def handle_feedback(cb: types.CallbackQuery):
            if cb.data in ("rate_up", "rate_down") and self.brain.last_interaction:
                reward = 1.0 if cb.data == "rate_up" else -0.5
                li = self.brain.last_interaction
                if reward > 0:
                    self.brain.record_training_sample(li["prompt"], li["thinking"], li["response"], source="tg_like", reward=reward, verified=True, evidence="explicit_user_positive_feedback")
                    await cb.answer("Положительная оценка сохранена; пример допущен к curated-датасету.")
                else:
                    self.brain.record_training_sample(li["prompt"], li["thinking"], li["response"], source="tg_dislike", reward=reward, verified=False, evidence="explicit_user_negative_feedback")
                    await cb.answer("Отрицательная оценка сохранена как сигнал для анализа, но не как позитивный обучающий пример.")

        @dp.message()
        async def handle_text(msg: types.Message):
            if not self._is_authorized(msg.from_user.id) or not msg.text:
                return
            res = await asyncio.to_thread(self.brain.think_and_act, msg.text)
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="👍 В датасет", callback_data="rate_up"),
                InlineKeyboardButton(text="👎 Отклонить", callback_data="rate_down")
            ]])
            await msg.answer(res["answer"][:4000], reply_markup=kb)

        await dp.start_polling(bot)
