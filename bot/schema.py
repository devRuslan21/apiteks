"""Структура базы данных Appwrite (TablesDB) и хранилища картинок."""

DATABASE_ID = "apiteks"
DATABASE_NAME = "APITEKS Telegram bot"
BUCKET_ID = "product_images"
BUCKET_NAME = "Картинки товаров"

PRODUCTS = "products"
VARIANTS = "variants"
USERS = "users"
ORDERS = "orders"

ORDER_STATUSES = {
    "new": "🆕 Новая",
    "processing": "🔄 В работе",
    "done": "✅ Выполнена",
    "canceled": "❌ Отменена",
}


def _col(kind, key, required=False, default=None, description="", **extra):
    return {"type": kind, "key": key, "required": required, "default": default, "description": description, **extra}


def _varchar(key, size, required=False, default=None, array=False, description=""):
    return _col("varchar", key, required, default, description, size=size, array=array)


def _index(key, columns, kind="key"):
    return {"key": key, "type": kind, "columns": columns}


TABLES = {
    PRODUCTS: {
        "name": "Товары",
        "columns": [
            _varchar("name", 255, required=True, description="Название товара"),
            _col("text", "description", description="Описание товара"),
            _col("float", "price", description="Цена за единицу в сумах. Пусто — в боте «цена по запросу»"),
            _varchar("unit", 16, default="м", description="Единица измерения: м, м², кг, шт, рулон…"),
            _col("float", "min_order", default=1, description="Минимальное количество для заказа (в единицах unit)"),
            _col("boolean", "active", default=True, description="false — товар скрыт в боте"),
            _col("integer", "sort", default=0, description="Порядок в списке товаров (меньше — выше)"),
            _varchar("image_ids", 64, array=True,
                     description="ID файлов из бакета «Картинки товаров» (Storage). Первая — главная"),
            _col("mediumtext", "specs", description="Технические характеристики"),
            _varchar("url", 512, description="Ссылка на страницу товара на сайте"),
            _varchar("slug", 255, description="Адрес страницы на apiteks.uz (заполняется импортом)"),
        ],
        "indexes": [_index("idx_sort", ["sort"]), _index("idx_active", ["active"])],
    },
    VARIANTS: {
        "name": "Марки товаров",
        "columns": [
            _varchar("product_id", 64, required=True, description="ID товара из таблицы «Товары»"),
            _varchar("title", 255, required=True, description="Название марки / позиции"),
            _varchar("group_name", 255, description="Группа марок"),
            _col("text", "specs", description="Характеристики марки"),
            _varchar("image_id", 64, description="ID файла из бакета «Картинки товаров»"),
            _col("integer", "sort", default=0, description="Порядок в списке марок"),
        ],
        "indexes": [_index("idx_product", ["product_id"])],
    },
    USERS: {
        "name": "Пользователи бота",
        "columns": [
            _col("bigint", "tg_id", required=True, description="Telegram ID"),
            _varchar("username", 64, description="Telegram username"),
            _varchar("full_name", 255, description="Имя в Telegram"),
            _varchar("phone", 32, description="Телефон"),
        ],
        "indexes": [_index("idx_tg_id", ["tg_id"], "unique")],
    },
    ORDERS: {
        "name": "Заявки",
        "columns": [
            _varchar("status", 32, required=True, description="new / processing / done / canceled"),
            _varchar("user_id", 32, required=True, description="Telegram ID клиента"),
            _varchar("contact_name", 255, required=True, description="Имя клиента"),
            _varchar("username", 64, description="Telegram username клиента"),
            _varchar("phone", 32, required=True, description="Телефон клиента"),
            _varchar("product_id", 64, required=True, description="ID товара"),
            _varchar("product_name", 255, required=True, description="Название товара на момент заказа"),
            _varchar("variant_title", 255, description="Выбранная марка"),
            _col("float", "quantity", required=True, description="Количество"),
            _varchar("unit", 16, required=True, description="Единица измерения"),
            _col("float", "price", description="Цена за единицу на момент заказа"),
            _col("float", "total", description="Сумма заказа"),
        ],
        "indexes": [_index("idx_user", ["user_id"]), _index("idx_status", ["status"])],
    },
}
