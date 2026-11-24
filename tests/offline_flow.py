"""Сквозной офлайн-тест: Appwrite заменён базой в памяти, Telegram — фиктивной сессией.

Запуск:  python tests/offline_flow.py
"""
import asyncio
import json
import os
import sys
from datetime import datetime
from itertools import count

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.update(BOT_TOKEN="123456:TEST", ADMIN_IDS="999", APPWRITE_PROJECT_ID="x", APPWRITE_API_KEY="x",
                  CATALOG_TTL_SECONDS="0")

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Contact, Message, PhotoSize, Update, User

from bot import keyboards as kb
from bot.catalog import catalog
from bot.db import Database
from bot.handlers import setup_routers
from bot.views import carousel_caption, details_texts
from scripts.import_catalog import guess_unit, short_id

ids = count(1)


def catalog_rows():
    data = json.load(open("data/catalog.json", encoding="utf-8"))
    products, variants = [], []
    site = [p for c in data["categories"] for p in c["products"]]
    for sort, p in enumerate(site, 1):
        pid = short_id("p_", p["slug"])
        products.append({"$id": pid, "name": p["name"], "description": p["description"], "specs": p["specs"],
                         "price": None, "unit": guess_unit(p["name"]), "min_order": 1, "active": True,
                         "sort": sort, "url": p["url"], "image_ids": [short_id("img_", i, 24) for i in p["images"]]})
        for idx, v in enumerate(p["variants"]):
            variants.append({"$id": short_id("v_", f"{p['slug']}|{idx}|{v['title']}"), "product_id": pid,
                             "title": v["title"], "group_name": v["group"], "specs": v["specs"],
                             "image_id": None, "sort": idx})
    return products, variants


class FakeDB(Database):
    def __init__(self):
        self.products, self.variants = catalog_rows()
        self.users, self.orders = {}, {}
        self.seq = count(1)

    async def load_catalog(self):
        return [dict(p) for p in self.products], [dict(v) for v in self.variants]

    async def image_bytes(self, file_id):
        return b"\xff\xd8fake"

    async def get_user(self, tg_id):
        return self.users.get(str(tg_id))

    async def upsert_user(self, tg_id, username, full_name, **extra):
        u = self.users.setdefault(str(tg_id), {"$id": str(tg_id)})
        u.update(tg_id=tg_id, username=username or "", full_name=full_name, **extra)
        return u

    async def create_order(self, data):
        o = {"$id": f"o{next(ids)}", "$sequence": str(next(self.seq)), "$createdAt": datetime.now().isoformat(),
             "status": "new", **data}
        self.orders[o["$id"]] = o
        return o

    async def get_order(self, order_id):
        return self.orders.get(order_id)

    async def user_orders(self, tg_id, limit=10):
        return [o for o in self.orders.values() if o["user_id"] == str(tg_id)][::-1][:limit]

    async def recent_orders(self, status=None, limit=15):
        return [o for o in self.orders.values() if not status or o["status"] == status][::-1][:limit]

    async def set_order_status(self, order_id, status):
        self.orders[order_id]["status"] = status
        return self.orders[order_id]


class MockSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.log, self.last = [], {}

    async def close(self):
        pass

    async def stream_content(self, *a, **k):
        yield b""

    def _msg(self, chat_id, text=None, caption=None, photo=False):
        m = Message(message_id=next(ids), date=datetime.now(), chat=Chat(id=chat_id, type="private"),
                    text=text, caption=caption,
                    photo=[PhotoSize(file_id=f"tg{next(ids)}", file_unique_id="u", width=1, height=1)] if photo else None)
        self.last[chat_id] = m
        return m

    async def make_request(self, bot, method, timeout=None):
        name, d = type(method).__name__, method.model_dump()
        self.log.append((name, d))
        chat_id = d.get("chat_id") or 0
        if name in ("SendMessage", "EditMessageText"):
            return self._msg(chat_id, text=d["text"])
        if name == "SendPhoto":
            return self._msg(chat_id, caption=d["caption"], photo=True)
        if name == "EditMessageMedia":
            return self._msg(chat_id, caption=d["media"]["caption"], photo=True)
        if name == "SendMediaGroup":
            return [self._msg(chat_id, photo=True) for _ in d["media"]]
        return True


async def main():
    session = MockSession()
    bot = Bot("123456:TEST", session=session)
    db = FakeDB()
    await catalog.load(db)
    caps = [len(carousel_caption(p)) for p in catalog.products]
    print(f"[static] товаров: {len(catalog.products)}, max подпись карусели: {max(caps)} (лимит 1024), "
          f"max блок подробностей: {max(len(c) for p in catalog.products for c in details_texts(p))} (лимит 4096)")
    assert max(caps) <= 1024

    dp = Dispatcher(storage=MemoryStorage(), db=db)
    dp.include_router(setup_routers())
    user = User(id=555, is_bot=False, first_name="Иван", username="ivan")
    admin = User(id=999, is_bot=False, first_name="Admin")
    uid = count(1)

    async def send(text=None, contact=None, who=user):
        m = Message(message_id=next(ids), date=datetime.now(), chat=Chat(id=who.id, type="private"),
                    from_user=who, text=text, contact=contact)
        n = len(session.log)
        await dp.feed_update(bot, Update(update_id=next(uid), message=m))
        return session.log[n:]

    async def press(data, who=user):
        cq = CallbackQuery(id=str(next(ids)), from_user=who, chat_instance="ci", data=data, message=session.last[who.id])
        n = len(session.log)
        await dp.feed_update(bot, Update(update_id=next(uid), callback_query=cq))
        return session.log[n:]

    def show(step, calls):
        names = [c[0] for c in calls if c[0] != "AnswerCallbackQuery"]
        texts = [(c[1].get("text") or c[1].get("caption") or (c[1].get("media") or {}).get("caption") or "")[:110]
                 .replace("\n", " ") for c in calls
                 if c[0] in ("SendMessage", "SendPhoto", "EditMessageText", "EditMessageMedia")]
        alerts = [c[1].get("text") for c in calls if c[0] == "AnswerCallbackQuery" and c[1].get("show_alert")]
        print(f"[{step}] {names} {texts}{' ALERT: ' + str(alerts) if alerts else ''}")
        return calls

    # Админ поставил цену и минимальный заказ в Appwrite — бот видит это без перезапуска.
    steel = next(p for p in db.products if p["name"] == "Стеклоткань")
    steel.update(price=45000, min_order=50)
    db.products[1]["active"] = False

    show("start", await send("/start"))
    show("товары", await send(kb.BTN_PRODUCTS))
    calls = show("▶️", await press(kb.Nav(action="item", page=1).pack()))
    assert calls[-1][1]["media"]["caption"].startswith(f"<b>{catalog.products[1].name}")
    show("список", await press(kb.Nav(action="list", page=0).pack()))
    show("список стр.3", await press(kb.Nav(action="list", page=2).pack()))
    idx = catalog.index_of(steel["$id"])
    calls = show("выбор из списка", await press(kb.Nav(action="item", page=idx).pack()))
    assert "45 000 сум" in calls[-1][1]["caption"] and "50 м" in calls[-1][1]["caption"]
    assert all(p.id != db.products[1]["$id"] for p in catalog.products), "скрытый товар виден"
    show("подробнее", await press(kb.Nav(action="info", id=steel["$id"]).pack()))
    show("заказать", await press(kb.Nav(action="order", id=steel["$id"]).pack()))
    show("марки стр.2", await press(kb.Nav(action="ovars", id=steel["$id"], page=1).pack()))
    variant = catalog.product_by_id[steel["$id"]].variants[3]
    show("марка", await press(kb.Nav(action="ovar", id=variant.id).pack()))
    show("мало", await send("10"))
    show("не число", await send("много"))
    show("количество", await send("120,5"))
    show("телефон", await send(contact=Contact(phone_number="998901234567", first_name="Иван", user_id=555)))
    calls = show("подтвердить", await press(kb.OrderCb(action="confirm").pack()))
    assert any("менеджер обязательно" in (c[1].get("text") or "") for c in calls)
    order = next(iter(db.orders.values()))
    print("[заявка]", {k: order[k] for k in ("product_name", "variant_title", "quantity", "unit", "price", "total", "phone")})
    assert order["total"] == 45000 * 120.5
    admin_msg = next(c for c in calls if c[0] == "SendMessage" and c[1]["chat_id"] == 999)
    print("[админу]", admin_msg[1]["text"].replace("\n", " | ")[:260])

    auto = next(p for p in db.products if p["name"] == "Ткань для аэрожелобов")
    show("заказ 2: товар", await press(kb.Nav(action="order", id=auto["$id"]).pack()))
    show("заказ 2: без марки", await press(kb.Nav(action="ovar_skip", id=auto["$id"]).pack()))
    show("заказ 2: кол-во (телефон уже есть)", await send("5"))
    show("заказ 2: отмена", await press(kb.OrderCb(action="cancel").pack()))
    show("мои заявки", await send(kb.BTN_ORDERS))
    show("статус (не админ)", await press(kb.AdminCb(id=order["$id"], status="processing").pack()))
    show("/orders_new", await send("/orders_new", who=admin))
    show("статус (админ)", await press(kb.AdminCb(id=order["$id"], status="processing").pack(), who=admin))
    assert db.orders[order["$id"]]["status"] == "processing"
    show("старая кнопка марки", await press(kb.Nav(action="ovar", id=variant.id).pack()))
    show("непонятный текст", await send("привет"))
    show("контакты", await send(kb.BTN_CONTACTS))
    print("OK: сценарий пройден без ошибок")


asyncio.run(main())
