"""Тексты и отправка сообщений с картинками из Appwrite."""
from __future__ import annotations

import logging
from html import escape

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, InputMediaPhoto, Message

from .catalog import Product
from .config import settings
from .db import Database
from .schema import ORDER_STATUSES

log = logging.getLogger(__name__)

# Appwrite file_id -> Telegram file_id, чтобы не скачивать картинку повторно.
_tg_file_ids: dict[str, str] = {}

CONTACTS_TEXT = (
    "<b>ООО «APITEKS»</b> — производство и поставка технического текстиля\n\n"
    "📍 Узбекистан, 100031, г. Ташкент, ул. Бешчинар 2А\n\n"
    "📞 Коммерческий директор: +998 (71) 255-40-43\n"
    "📞 Отдел сбыта: +998 (71) 255-66-80\n"
    "📞 Главный инженер: +998 (71) 255-09-75\n"
    "🕗 Пн—Пт, 8:00–17:00\n\n"
    "✉️ apitextile@mail.ru\n"
    "🌐 <a href=\"https://apiteks.uz\">apiteks.uz</a>"
)


def cut(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut_at = text.rfind(" ", 0, limit)
    return text[: cut_at if cut_at > limit // 2 else limit].rstrip(" ,.;:") + "…"


def fmt_num(value: float) -> str:
    if value == int(value):
        return f"{int(value):,}".replace(",", " ")
    return f"{value:,.2f}".replace(",", " ").replace(".", ",").rstrip("0").rstrip(",")


def price_line(product: Product) -> str:
    if product.price is None:
        return "💰 Цена: <b>по запросу</b>"
    return f"💰 Цена: <b>{fmt_num(product.price)} {settings.currency}</b> за 1 {escape(product.unit)}"


def min_order_line(product: Product) -> str:
    return f"📦 Минимальный заказ: <b>{fmt_num(product.min_order)} {escape(product.unit)}</b>"


def carousel_caption(product: Product) -> str:
    parts = [f"<b>{escape(product.name)}</b>", f"{price_line(product)}\n{min_order_line(product)}"]
    if product.description:
        parts.append(escape(cut(product.description, 350)))
    return "\n\n".join(parts)


def details_texts(product: Product) -> list[str]:
    text = f"<b>{escape(product.name)}</b>\n\n{price_line(product)}\n{min_order_line(product)}"
    if product.description:
        text += f"\n\n{escape(product.description)}"
    if product.specs:
        text += f"\n\n<b>Характеристики</b>\n{escape(product.specs)}"
    if product.variants:
        titles = "\n".join(f"• {escape(v.title)}" for v in product.variants[:60])
        more = f"\n…и ещё {len(product.variants) - 60}" if len(product.variants) > 60 else ""
        text += f"\n\n<b>Доступные марки ({len(product.variants)})</b>\n{titles}{more}"
    chunks, rest = [], text
    while rest:
        if len(rest) <= 4000:
            chunks.append(rest)
            break
        split = rest.rfind("\n", 0, 4000)
        split = split if split > 1000 else 4000
        chunks.append(rest[:split])
        rest = rest[split:].lstrip("\n")
    return chunks


def order_summary(data: dict) -> str:
    lines = [f"🛒 <b>{escape(data['product_name'])}</b>"]
    if data.get("variant_title"):
        lines.append(f"Марка: {escape(data['variant_title'])}")
    lines.append(f"Количество: <b>{fmt_num(data['quantity'])} {escape(data['unit'])}</b>")
    if data.get("price") is not None:
        lines.append(f"Цена: {fmt_num(data['price'])} {settings.currency} за 1 {escape(data['unit'])}")
        lines.append(f"Сумма: <b>{fmt_num(data['total'])} {settings.currency}</b>")
    else:
        lines.append("Цена: по запросу — менеджер рассчитает стоимость")
    return "\n".join(lines)


def order_text(order: dict, for_admin: bool = False) -> str:
    status = ORDER_STATUSES.get(order["status"], order["status"])
    lines = [
        f"<b>Заявка №{order['$sequence']}</b> от {order['$createdAt'][:16].replace('T', ' ')} (UTC)",
        f"Статус: <b>{status}</b>",
        "",
        order_summary(order),
    ]
    if for_admin:
        who = f"<a href=\"tg://user?id={order['user_id']}\">{escape(order['contact_name'])}</a>"
        if order.get("username"):
            who += f" (@{escape(order['username'])})"
        lines += ["", f"👤 {who}", f"📞 {escape(order['phone'])}"]
    return "\n".join(lines)


async def _photo_input(db: Database, file_id: str):
    if file_id in _tg_file_ids:
        return _tg_file_ids[file_id]
    data = await db.image_bytes(file_id)
    return BufferedInputFile(data, filename=f"{file_id}.jpg")


def _remember(file_id: str, message: Message | bool | None) -> None:
    if isinstance(message, Message) and message.photo:
        _tg_file_ids[file_id] = message.photo[-1].file_id


async def send_card(
    target: Message,
    db: Database,
    text: str,
    kb: InlineKeyboardMarkup | None,
    image_id: str | None,
) -> None:
    if image_id:
        try:
            sent = await target.answer_photo(await _photo_input(db, image_id), caption=text, reply_markup=kb)
            _remember(image_id, sent)
            return
        except Exception as exc:  # картинка недоступна — показываем карточку без неё
            log.warning("Не удалось отправить фото %s: %s", image_id, exc)
    await target.answer(text, reply_markup=kb, disable_web_page_preview=True)


async def show_card(
    call: CallbackQuery,
    db: Database,
    text: str,
    kb: InlineKeyboardMarkup | None,
    image_id: str | None = None,
) -> None:
    """Показывает карточку вместо текущего сообщения (редактирует или пересоздаёт его)."""
    msg = call.message
    if not isinstance(msg, Message):
        return
    try:
        if image_id and msg.photo:
            edited = await msg.edit_media(
                InputMediaPhoto(media=await _photo_input(db, image_id), caption=text), reply_markup=kb
            )
            _remember(image_id, edited)
            return
        if not image_id and not msg.photo:
            await msg.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
            return
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return
        log.debug("Редактирование не удалось: %s", exc)
    except Exception as exc:
        log.warning("Не удалось обновить карточку: %s", exc)
    try:
        await msg.delete()
    except TelegramBadRequest:
        pass
    await send_card(msg, db, text, kb, image_id)


async def send_album(target: Message, db: Database, image_ids: list[str], caption: str) -> None:
    for start in range(0, len(image_ids), 10):
        media, used_ids = [], []
        for file_id in image_ids[start:start + 10]:
            try:
                media.append(InputMediaPhoto(
                    media=await _photo_input(db, file_id),
                    caption=caption if start == 0 and not media else None,
                ))
                used_ids.append(file_id)
            except Exception as exc:
                log.warning("Фото %s пропущено: %s", file_id, exc)
        if not media:
            continue
        sent = await target.answer_media_group(media)
        for file_id, message in zip(used_ids, sent):
            _remember(file_id, message)
