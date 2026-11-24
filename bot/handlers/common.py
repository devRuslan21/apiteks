from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from ..config import settings
from ..db import Database
from ..views import CONTACTS_TEXT

router = Router(name="common")


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.full_name)
    await message.answer(
        f"Здравствуйте, {escape(user.first_name or user.full_name)}! 👋\n\n"
        "Это бот компании <b>APITEKS</b> — производителя технического текстиля.\n\n"
        "Нажмите <b>«🛍 Товары»</b>, чтобы посмотреть продукцию, цены и оформить заявку.",
        reply_markup=kb.main_menu(),
    )


@router.message(Command("help"))
async def help_cmd(message: Message) -> None:
    text = (
        "🛍 <b>Товары</b> — продукция с фото, ценами и минимальным заказом\n"
        "📦 <b>Мои заявки</b> — ваши заявки и их статусы\n"
        "☎️ <b>Контакты</b> — телефоны и адрес"
    )
    if is_admin(message.from_user.id):
        text += (
            "\n\n<b>Администратору:</b>\n/orders — последние заявки\n/orders_new — новые заявки\n"
            "/reload — сразу перечитать товары из Appwrite"
        )
    await message.answer(text)


@router.message(F.text == kb.BTN_CANCEL)
@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=kb.main_menu())


@router.message(F.text == kb.BTN_CONTACTS)
async def contacts(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(CONTACTS_TEXT, disable_web_page_preview=True)


@router.callback_query(F.data == "noop")
async def noop(call: CallbackQuery) -> None:
    await call.answer()


fallback_router = Router(name="fallback")


@fallback_router.message(StateFilter(None))
async def unknown(message: Message) -> None:
    await message.answer("Нажмите «🛍 Товары», чтобы посмотреть продукцию.", reply_markup=kb.main_menu())
