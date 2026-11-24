from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .catalog import Product
from .schema import ORDER_STATUSES

BTN_PRODUCTS = "🛍 Товары"
BTN_ORDERS = "📦 Мои заявки"
BTN_CONTACTS = "☎️ Контакты"
BTN_CANCEL = "✖️ Отмена"
BTN_SEND_PHONE = "📱 Отправить мой номер"

LIST_PAGE = 10
VARIANTS_PAGE = 8


class Nav(CallbackData, prefix="n"):
    action: str
    id: str = ""
    page: int = 0


class OrderCb(CallbackData, prefix="o"):
    action: str


class AdminCb(CallbackData, prefix="adm"):
    id: str
    status: str


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_PRODUCTS)], [KeyboardButton(text=BTN_ORDERS), KeyboardButton(text=BTN_CONTACTS)]],
        resize_keyboard=True,
    )


def phone_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_SEND_PHONE, request_contact=True)], [KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def short(text: str, limit: int = 48) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _btn(text: str, action: str, item_id: str = "", page: int = 0) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=Nav(action=action, id=item_id, page=page).pack())


def carousel_kb(product: Product, index: int, total: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if total > 1:
        b.row(
            _btn("◀️", "item", page=(index - 1) % total),
            InlineKeyboardButton(text=f"{index + 1} / {total}", callback_data="noop"),
            _btn("▶️", "item", page=(index + 1) % total),
        )
    b.row(_btn("ℹ️ Подробнее", "info", product.id), _btn("🛒 Заказать", "order", product.id))
    b.row(_btn("📋 Список всех товаров", "list", page=index // LIST_PAGE))
    return b.as_markup()


def products_list_kb(products: list[Product], page: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    start = page * LIST_PAGE
    for i, p in enumerate(products[start:start + LIST_PAGE], start):
        b.row(_btn(f"{i + 1}. {short(p.name)}", "item", page=i))
    pages = (len(products) + LIST_PAGE - 1) // LIST_PAGE
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(_btn("◀️", "list", page=page - 1))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(_btn("▶️", "list", page=page + 1))
        b.row(*nav)
    return b.as_markup()


def details_kb(product: Product, index: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("🛒 Заказать", "order", product.id))
    if len(product.image_ids) > 1:
        b.row(_btn(f"🖼 Все фото ({len(product.image_ids)})", "photos", product.id))
    if product.url:
        b.row(InlineKeyboardButton(text="🌐 Открыть на apiteks.uz", url=product.url))
    b.row(_btn("⬅️ Назад к товарам", "item", page=index))
    return b.as_markup()


def variants_kb(product: Product, page: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    start = page * VARIANTS_PAGE
    for v in product.variants[start:start + VARIANTS_PAGE]:
        b.row(_btn(short(v.title), "ovar", v.id))
    pages = (len(product.variants) + VARIANTS_PAGE - 1) // VARIANTS_PAGE
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(_btn("◀️", "ovars", product.id, page - 1))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(_btn("▶️", "ovars", product.id, page + 1))
        b.row(*nav)
    b.row(_btn("➡️ Не знаю / уточню у менеджера", "ovar_skip", product.id))
    b.row(InlineKeyboardButton(text="✖️ Отмена", callback_data=OrderCb(action="cancel").pack()))
    return b.as_markup()


def cancel_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✖️ Отмена", callback_data=OrderCb(action="cancel").pack()),
    ]])


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить заявку", callback_data=OrderCb(action="confirm").pack())],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data=OrderCb(action="cancel").pack())],
    ])


def admin_order_kb(order: dict) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for status, label in ORDER_STATUSES.items():
        if status not in (order["status"], "new"):
            b.button(text=label, callback_data=AdminCb(id=order["$id"], status=status).pack())
    b.adjust(3)
    if order.get("username"):
        b.row(InlineKeyboardButton(text="💬 Написать клиенту", url=f"https://t.me/{order['username']}"))
    return b.as_markup()
