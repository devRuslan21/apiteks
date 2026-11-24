"""Создаёт в Appwrite базу данных, таблицы, колонки, индексы и бакет для картинок.

Скрипт идемпотентный: уже созданные объекты пропускаются.
Запуск:  python -m scripts.setup_appwrite
"""
from __future__ import annotations

import sys
import time

from appwrite.enums.tables_db_index_type import TablesDBIndexType
from appwrite.exception import AppwriteException
from appwrite.services.storage import Storage
from appwrite.services.tables_db import TablesDB

from bot import schema
from bot.config import settings
from bot.db import make_client

DB = schema.DATABASE_ID


def exists(fn, **kwargs) -> bool:
    try:
        fn(**kwargs)
        return True
    except AppwriteException as exc:
        if exc.code == 404:
            return False
        raise


def ensure_database(t: TablesDB) -> None:
    if exists(t.get, database_id=DB):
        print(f"= база данных {DB}")
        return
    t.create(database_id=DB, name=schema.DATABASE_NAME)
    print(f"+ база данных {DB}")


def create_column(t: TablesDB, table_id: str, col: dict) -> None:
    common = {
        "database_id": DB,
        "table_id": table_id,
        "key": col["key"],
        "required": col["required"],
        "description": col.get("description") or None,
    }
    default = None if col["required"] else col.get("default")
    kind = col["type"]
    if kind == "boolean":
        t.create_boolean_column(**common, default=default)
        return
    if kind == "varchar":
        t.create_varchar_column(**common, size=col["size"], default=default, array=col.get("array") or None)
    elif kind == "text":
        t.create_text_column(**common)
    elif kind == "mediumtext":
        t.create_mediumtext_column(**common)
    elif kind == "integer":
        t.create_integer_column(**common, default=default)
    elif kind == "bigint":
        t.create_big_int_column(**common, default=default)
    elif kind == "float":
        t.create_float_column(**common, default=default)
    else:
        raise ValueError(f"Неизвестный тип колонки: {kind}")


def wait_available(list_fn, attr: str, table_id: str, timeout: int = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        items = getattr(list_fn(database_id=DB, table_id=table_id), attr)
        statuses = {getattr(i, "key", "?"): getattr(i, "status", "available") for i in items}
        failed = [k for k, s in statuses.items() if s == "failed"]
        if failed:
            raise SystemExit(f"Appwrite не смог создать {attr} {failed} в таблице {table_id}")
        if all(s == "available" for s in statuses.values()):
            return
        time.sleep(1)
    raise SystemExit(f"Таймаут ожидания {attr} в таблице {table_id}")


def ensure_table(t: TablesDB, table_id: str, spec: dict) -> None:
    if exists(t.get_table, database_id=DB, table_id=table_id):
        print(f"= таблица {table_id}")
    else:
        # Права не выдаются: доступ к данным только у бота по API-ключу.
        t.create_table(database_id=DB, table_id=table_id, name=spec["name"], permissions=[], row_security=False)
        print(f"+ таблица {table_id}")

    existing = {c.key for c in t.list_columns(database_id=DB, table_id=table_id).columns}
    for col in spec["columns"]:
        if col["key"] in existing:
            continue
        create_column(t, table_id, col)
        print(f"  + колонка {table_id}.{col['key']} ({col['type']})")
    wait_available(t.list_columns, "columns", table_id)

    existing_idx = {i.key for i in t.list_indexes(database_id=DB, table_id=table_id).indexes}
    for idx in spec["indexes"]:
        if idx["key"] in existing_idx:
            continue
        t.create_index(
            database_id=DB,
            table_id=table_id,
            key=idx["key"],
            type=TablesDBIndexType(idx["type"]),
            columns=idx["columns"],
        )
        print(f"  + индекс {table_id}.{idx['key']}")
    wait_available(t.list_indexes, "indexes", table_id)


def ensure_bucket(s: Storage) -> None:
    if exists(s.get_bucket, bucket_id=schema.BUCKET_ID):
        print(f"= бакет {schema.BUCKET_ID}")
        return
    s.create_bucket(
        bucket_id=schema.BUCKET_ID,
        name=schema.BUCKET_NAME,
        permissions=[],
        file_security=False,
        maximum_file_size=30 * 1024 * 1024,
        allowed_file_extensions=["jpg", "jpeg", "png", "webp", "gif"],
        encryption=False,
        antivirus=False,
    )
    print(f"+ бакет {schema.BUCKET_ID}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    client = make_client(settings)
    tables = TablesDB(client)
    print(f"Appwrite: {settings.appwrite_endpoint}, проект {settings.appwrite_project_id}")
    ensure_database(tables)
    for table_id, spec in schema.TABLES.items():
        ensure_table(tables, table_id, spec)
    ensure_bucket(Storage(client))
    print("Готово: структура базы создана.")


if __name__ == "__main__":
    main()
