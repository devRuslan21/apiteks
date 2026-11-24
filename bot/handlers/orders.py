import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from .. import keyboards as kb
from ..catalog import catalog
from ..db import Database
from ..schema import ORDER_STATUSES
from ..views import fmt_num, order_text
from .common import is_admin

log = logging.getLogger(__name__)
router = Router(name="orders")


@router.message(F.text == kb.BTN_ORDERS)
@router.message(Command("my_orders"))
async def my_orders(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    orders = await db.user_orders(message.from_user.id)
    if not orders:
        await message.answer("У вас пока нет заявок. Выберите товар в разделе «🛍 Товары».")
        return
    lines = ["📦 <b>Ваши заявки</b> (последние 10)\n"]
    for o in orders:
        status = ORDER_STATUSES.get(o["status"], o["status"])
        lines.append(
            f"№{o['$sequence']} от {o['$createdAt'][:10]} — {escape(o['product_name'])}, "
            f"{fmt_num(o['quantity'])} {escape(o['unit'])}\n{status}\n"
        )
    await message.answer("\n".join(lines))


# ---------- администратор ----------

@router.message(Command("orders", "orders_new"))
async def admin_orders(message: Message, db: Database) -> None:
    if not is_admin(message.from_user.id):
        return
    only_new = message.text.startswith("/orders_new")
    orders = await db.recent_orders(status="new" if only_new else None, limit=10)
    if not orders:
        await message.answer("Заявок нет.")
        return
    for order in reversed(orders):
        await message.answer(order_text(order, for_admin=True), reply_markup=kb.admin_order_kb(order))


@router.message(Command("reload"))
async def admin_reload(message: Message, db: Database) -> None:
    if not is_admin(message.from_user.id):
        return
    await catalog.load(db)
    await message.answer(f"✅ Товары перечитаны из Appwrite: {len(catalog.products)} шт.")


@router.callback_query(kb.AdminCb.filter())
async def admin_set_status(call: CallbackQuery, callback_data: kb.AdminCb, db: Database, bot: Bot) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("Недостаточно прав", show_alert=True)
        return
    if callback_data.status not in ORDER_STATUSES:
        await call.answer()
        return
    order = await db.set_order_status(callback_data.id, callback_data.status)
    label = ORDER_STATUSES[order["status"]]
    await call.message.edit_text(
        order_text(order, for_admin=True) + f"\n\n✏️ Статус изменил: {call.from_user.full_name}",
        reply_markup=kb.admin_order_kb(order),
    )
    await call.answer(f"Статус: {label}")
    try:
        await bot.send_message(int(order["user_id"]), f"📦 Статус вашей заявки №{order['$sequence']}: <b>{label}</b>")
    except Exception as exc:
        log.warning("Не удалось уведомить клиента %s: %s", order["user_id"], exc)
