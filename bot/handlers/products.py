from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from ..catalog import catalog
from ..config import settings
from ..db import Database
from ..views import carousel_caption, details_texts, send_album, send_card, show_card

router = Router(name="products")

EMPTY_TEXT = "Товары пока не добавлены. Загляните позже."


async def refresh(db: Database) -> None:
    await catalog.refresh(db, settings.catalog_ttl_seconds)


def carousel(index: int):
    total = len(catalog.products)
    index %= total
    product = catalog.products[index]
    image = product.image_ids[0] if product.image_ids else None
    return carousel_caption(product), kb.carousel_kb(product, index, total), image


@router.message(F.text == kb.BTN_PRODUCTS)
@router.message(Command("products"))
async def open_products(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    await refresh(db)
    if not catalog.products:
        await message.answer(EMPTY_TEXT)
        return
    await send_card(message, db, *carousel(0))


@router.callback_query(kb.Nav.filter(F.action == "item"))
async def show_item(call: CallbackQuery, callback_data: kb.Nav, db: Database) -> None:
    await refresh(db)
    if not catalog.products:
        await call.answer(EMPTY_TEXT, show_alert=True)
        return
    await call.answer()
    await show_card(call, db, *carousel(callback_data.page))


@router.callback_query(kb.Nav.filter(F.action == "list"))
async def show_list(call: CallbackQuery, callback_data: kb.Nav, db: Database) -> None:
    await refresh(db)
    if not catalog.products:
        await call.answer(EMPTY_TEXT, show_alert=True)
        return
    await call.answer()
    text = f"📋 <b>Все товары</b> ({len(catalog.products)})\n\nВыберите товар:"
    await show_card(call, db, text, kb.products_list_kb(catalog.products, callback_data.page))


@router.callback_query(kb.Nav.filter(F.action == "info"))
async def show_details(call: CallbackQuery, callback_data: kb.Nav, db: Database) -> None:
    await refresh(db)
    product = catalog.product_by_id.get(callback_data.id)
    if product is None:
        await call.answer("Товар больше не доступен", show_alert=True)
        return
    await call.answer()
    chunks = details_texts(product)
    markup = kb.details_kb(product, catalog.index_of(product.id))
    for i, chunk in enumerate(chunks):
        last = i == len(chunks) - 1
        await call.message.answer(chunk, reply_markup=markup if last else None, disable_web_page_preview=True)


@router.callback_query(kb.Nav.filter(F.action == "photos"))
async def show_photos(call: CallbackQuery, callback_data: kb.Nav, db: Database) -> None:
    product = catalog.product_by_id.get(callback_data.id)
    if product is None or not product.image_ids:
        await call.answer("Фото нет", show_alert=True)
        return
    await call.answer("Загружаю фото…")
    await send_album(call.message, db, product.image_ids, product.name)
    await call.message.answer(
        f"Фото: {escape(product.name)}",
        reply_markup=kb.details_kb(product, catalog.index_of(product.id)),
    )
