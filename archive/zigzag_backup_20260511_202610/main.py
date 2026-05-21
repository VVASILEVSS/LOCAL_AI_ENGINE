import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from core.db import set_setting
set_setting('symbols', ["BTCUSDT", "XAUTUSDT"])
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage

from core.config import TOKEN
from core.handlers import router
from core.scheduler import start_scheduler
from core.utils import load_markets_cache

async def main():
    logging.basicConfig(level=logging.INFO)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    # ✅ Загружаем кэш рынков для мгновенного форматирования
    await load_markets_cache()
    
    session = AiohttpSession()
    bot = Bot(token=TOKEN, session=session)
    
    print("✅ Бот запущен! Ожидание сообщений...")
    print(f"📂 Папка проекта: {os.getcwd()}")
    
    # 🕒 Запуск почасового планировщика
    start_scheduler(bot)
    
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n❌ Бот остановлен.")