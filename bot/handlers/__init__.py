from aiogram import Router

from . import common, order, orders, products


def setup_routers() -> Router:
    root = Router(name="root")
    # Порядок важен: кнопки меню должны срабатывать раньше шагов оформления заявки,
    # а подсказка на неизвестные сообщения — последней.
    root.include_routers(common.router, products.router, orders.router, order.router, common.fallback_router)
    return root
