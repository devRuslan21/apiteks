import logging
import re
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from ..catalog import Product, catalog
from ..config import settings
from ..db import Database
from ..states import OrderForm
from ..views import fmt_num, order_summary, order_text, price_line

log = logging.getLogger(__name__)
router = Router(name="order")

MANAGER_WILL_WRITE = (
    "Наш менеджер обязательно свяжется с вами в ближайшее время, чтобы уточнить детали "
    "и согласовать доставку. Пожалуйста, ожидайте 🙂"
)


def quantity_prompt(product: Product, variant_title: str | None) -> str:
    title = escape(product.name) + (f" — {escape(variant_title)}" if variant_title else "")
    return (
        f"🛒 <b>{title}</b>\n\n{price_line(product)}\n\n"
        f"Сколько <b>{escape(product.unit)}</b> вам нужно?\n"
        f"Минимальный заказ — <b>{fmt_num(product.min_order)} {escape(product.unit)}</b>.\n\n"
        "Напишите количество числом:"
    )


@router.callback_query(kb.Nav.filter(F.action == "order"))
async def order_start(call: CallbackQuery, callback_data: kb.Nav, state: FSMContext) -> None:
    product = catalog.product_by_id.get(callback_data.id)
    if product is None:
        await call.answer("Товар больше не доступен", show_alert=True)
        return
    await state.clear()
    await state.update_data(product_id=product.id, variant_title=None)
    await call.answer()
    if product.variants:
        await state.set_state(OrderForm.variant)
        await call.message.answer(
            f"🛒 <b>{escape(product.name)}</b>\n\nВыберите марку:",
            reply_markup=kb.variants_kb(product, 0),
        )
        return
    await state.set_state(OrderForm.quantity)
    await call.message.answer(quantity_prompt(product, None), reply_markup=kb.cancel_inline_kb())


@router.callback_query(OrderForm.variant, kb.Nav.filter(F.action == "ovars"))
async def variants_page(call: CallbackQuery, callback_data: kb.Nav) -> None:
    product = catalog.product_by_id.get(callback_data.id)
    if product is not None:
        await call.message.edit_reply_markup(reply_markup=kb.variants_kb(product, callback_data.page))
    await call.answer()


@router.callback_query(OrderForm.variant, kb.Nav.filter(F.action.in_({"ovar", "ovar_skip"})))
async def variant_chosen(call: CallbackQuery, callback_data: kb.Nav, state: FSMContext) -> None:
    data = await state.get_data()
    product = catalog.product_by_id.get(data["product_id"])
    if product is None:
        await state.clear()
        await call.answer("Товар больше не доступен", show_alert=True)
        return
    variant = catalog.variant_by_id.get(callback_data.id) if callback_data.action == "ovar" else None
    variant_title = variant.title if variant else None
    await state.update_data(variant_title=variant_title)
    await state.set_state(OrderForm.quantity)
    await call.message.edit_text(quantity_prompt(product, variant_title), reply_markup=kb.cancel_inline_kb())
    await call.answer()


@router.message(OrderForm.quantity, F.text)
async def quantity_entered(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    product = catalog.product_by_id.get(data["product_id"])
    if product is None:
        await state.clear()
        await message.answer("Товар больше не доступен.", reply_markup=kb.main_menu())
        return
    try:
        quantity = float(message.text.strip().replace(",", ".").replace(" ", ""))
    except ValueError:
        await message.answer("Напишите количество числом, например: 150 или 12,5")
        return
    if quantity < product.min_order or quantity > 10_000_000:
        await message.answer(
            f"Минимальный заказ — {fmt_num(product.min_order)} {escape(product.unit)}. Напишите количество не меньше."
        )
        return
    await state.update_data(
        product_name=product.name,
        quantity=quantity,
        unit=product.unit,
        price=product.price,
        total=round(product.price * quantity, 2) if product.price is not None else None,
    )
    user = await db.get_user(message.from_user.id)
    if user and user.get("phone"):
        await state.update_data(phone=user["phone"])
        await ask_confirm(message, state)
        return
    await state.set_state(OrderForm.phone)
    await message.answer(
        "📞 Отправьте номер телефона кнопкой ниже или напишите его, чтобы менеджер мог с вами связаться:",
        reply_markup=kb.phone_menu(),
    )


@router.message(OrderForm.phone, F.contact | F.text)
async def phone_entered(message: Message, state: FSMContext, db: Database) -> None:
    phone = message.contact.phone_number if message.contact else message.text.strip()
    digits = re.sub(r"\D", "", phone)
    if not 9 <= len(digits) <= 15:
        await message.answer("Похоже, номер указан неверно. Напишите его полностью, например +998 90 123-45-67.")
        return
    if message.contact and not phone.startswith("+"):
        phone = "+" + digits
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.full_name, phone=phone[:32])
    await state.update_data(phone=phone[:32])
    await message.answer("✅ Номер сохранён.", reply_markup=kb.main_menu())
    await ask_confirm(message, state)


async def ask_confirm(message: Message, state: FSMContext) -> None:
    await state.set_state(OrderForm.confirm)
    data = await state.get_data()
    await message.answer(
        f"Проверьте заявку 👇\n\n{order_summary(data)}\n📞 {escape(data['phone'])}",
        reply_markup=kb.confirm_kb(),
    )


@router.callback_query(kb.OrderCb.filter(F.action == "cancel"))
async def order_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text("Заявка отменена.")
    await call.answer()


@router.callback_query(OrderForm.confirm, kb.OrderCb.filter(F.action == "confirm"))
async def order_confirm(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    data = await state.get_data()
    tg = call.from_user
    await call.answer("Отправляю заявку…")
    try:
        order = await db.create_order({
            "user_id": str(tg.id),
            "contact_name": tg.full_name[:255],
            "username": (tg.username or "")[:64],
            "phone": data["phone"],
            "product_id": data["product_id"],
            "product_name": data["product_name"][:255],
            "variant_title": (data.get("variant_title") or "")[:255],
            "quantity": data["quantity"],
            "unit": data["unit"],
            "price": data.get("price"),
            "total": data.get("total"),
        })
    except Exception:
        log.exception("Не удалось создать заявку")
        await call.message.answer("⚠️ Не удалось отправить заявку. Попробуйте ещё раз чуть позже или позвоните нам.")
        return
    await state.clear()
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(
        f"✅ <b>Заявка №{order['$sequence']} отправлена!</b>\n\n{MANAGER_WILL_WRITE}",
        reply_markup=kb.main_menu(),
    )
    await notify_admins(bot, order)


@router.callback_query(kb.OrderCb.filter(F.action == "confirm"))
@router.callback_query(kb.Nav.filter(F.action.in_({"ovar", "ovar_skip", "ovars"})))
async def order_expired(call: CallbackQuery) -> None:
    await call.answer("Заявка устарела. Откройте товар и нажмите «Заказать» ещё раз.", show_alert=True)


async def notify_admins(bot: Bot, order: dict) -> None:
    recipients = list(settings.admin_ids)
    if settings.orders_chat_id and settings.orders_chat_id not in recipients:
        recipients.insert(0, settings.orders_chat_id)
    if not recipients:
        log.warning("ADMIN_IDS / ORDERS_CHAT_ID не заданы — заявка №%s никому не отправлена", order["$sequence"])
        return
    text = "🔔 <b>Новая заявка из Telegram-бота</b>\n\n" + order_text(order, for_admin=True)
    for chat_id in recipients:
        try:
            await bot.send_message(chat_id, text, reply_markup=kb.admin_order_kb(order))
        except Exception as exc:
            log.warning("Не удалось уведомить %s: %s", chat_id, exc)
