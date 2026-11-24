from aiogram.fsm.state import State, StatesGroup


class OrderForm(StatesGroup):
    variant = State()
    quantity = State()
    phone = State()
    confirm = State()
