"""Запуск бота:  python -m bot"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from .catalog import catalog
from .config import settings
from .db import Database
from .handlers import setup_routers

log = logging.getLogger("bot")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not settings.bot_token:
        raise SystemExit("Заполните BOT_TOKEN в .env")
    db = Database(settings)
    await catalog.load(db)
    if not catalog.products:
        log.warning("В Appwrite нет активных товаров — запустите: python -m scripts.import_catalog")

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage(), db=db)
    dp.include_router(setup_routers())
    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="products", description="Товары"),
        BotCommand(command="my_orders", description="Мои заявки"),
        BotCommand(command="help", description="Помощь"),
    ])
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
