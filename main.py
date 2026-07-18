import asyncio
import os

# Фикс DNS для aiohttp на Windows: aiodns не работает, принудительно ThreadedResolver
import aiohttp
from aiohttp.resolver import ThreadedResolver
_orig_init = aiohttp.TCPConnector.__init__
def _patched_init(self, *a, **kw):
    if 'resolver' not in kw or kw['resolver'] is None:
        kw['resolver'] = ThreadedResolver()
    return _orig_init(self, *a, **kw)
aiohttp.TCPConnector.__init__ = _patched_init

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
    # ОТКЛЮЧЕН: scheduler вызывает run_hourly_analysis без LLM params
    # → fallback на LM Studio localhost:1234 которого нет.
    # Включить после подключения LLM params к scheduler.
    # start_scheduler(bot)

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