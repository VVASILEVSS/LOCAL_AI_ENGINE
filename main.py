import asyncio
import os

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage

from core.config import TOKEN
from core.handlers import router
from core.logging_setup import setup_logging
from core.scheduler import start_scheduler, scheduler
from core.utils import load_markets_cache

logger = setup_logging()


async def main() -> None:
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # Загружаем кэш рынков для мгновенного форматирования
    await load_markets_cache()

    session = AiohttpSession()
    bot = Bot(token=TOKEN, session=session)

    logger.info("Бот запущен! Ожидание сообщений...")
    logger.info("Папка проекта: %s", os.getcwd())

    # Запуск планировщика автоанализа
    start_scheduler(bot)

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        logger.info("Бот остановлен.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен (Ctrl+C).")