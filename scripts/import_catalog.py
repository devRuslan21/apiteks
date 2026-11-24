"""Загружает товары с сайта (data/catalog.json) и их картинки в Appwrite.

По умолчанию добавляет только новые товары и марки, существующие строки не трогает,
поэтому цены и правки, сделанные в консоли Appwrite, сохраняются.
С флагом --overwrite обновляет у существующих товаров данные с сайта (название, описание,
характеристики, картинки, ссылку), но никогда не меняет price, unit, min_order, active и sort.
Строки не удаляются никогда.

Запуск:  python -m scripts.import_catalog [--overwrite]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from appwrite.exception import AppwriteException
from appwrite.input_file import InputFile
from appwrite.query import Query
from appwrite.services.storage import Storage
from appwrite.services.tables_db import TablesDB

from bot import schema
from bot.config import ROOT, settings
from bot.db import make_client

CATALOG_FILE = ROOT / "data" / "catalog.json"
DB = schema.DATABASE_ID

# Начальная единица измерения по ключевым словам в названии; дальше правится в Appwrite.
UNIT_RULES = [
    ("пленк", "м²"), ("плёнк", "м²"), ("знак", "шт"), ("фильтр", "шт"), ("картридж", "шт"),
    ("электрод", "шт"), ("набивк", "кг"), ("шнур", "кг"), ("бумаг", "м²"), ("мембран", "м²"),
    ("полилен", "м²"), ("рукав", "м"), ("лент", "м"), ("ткан", "м"),
]


def guess_unit(name: str) -> str:
    low = name.lower()
    return next((unit for key, unit in UNIT_RULES if key in low), "м")


def short_id(prefix: str, value: str, length: int = 20) -> str:
    return prefix + hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


def upload_images(storage: Storage, paths: list[str]) -> dict[str, str]:
    """Загружает картинки в бакет, возвращает {локальный путь: file_id}."""

    def upload(path: str) -> tuple[str, str | None]:
        file_id = short_id("img_", path, 24)
        try:
            storage.get_file(bucket_id=schema.BUCKET_ID, file_id=file_id)
            return path, file_id
        except AppwriteException as exc:
            if exc.code != 404:
                raise
        full = ROOT / path
        if not full.exists():
            print(f"  ! нет файла {path}")
            return path, None
        try:
            storage.create_file(bucket_id=schema.BUCKET_ID, file_id=file_id, file=InputFile.from_path(str(full)))
        except AppwriteException as exc:
            print(f"  ! не загружена {path}: {exc.message}")
            return path, None
        return path, file_id

    print(f"Загружаю картинки в бакет {schema.BUCKET_ID}: {len(paths)} шт.")
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = {p: f for p, f in pool.map(upload, paths) if f}
    print(f"  картинок в Appwrite: {len(result)}")
    return result


def all_ids(tables: TablesDB, table_id: str) -> set[str]:
    ids: set[str] = set()
    cursor = None
    while True:
        queries = [Query.select(["$id"]), Query.limit(100)]
        if cursor:
            queries.append(Query.cursor_after(cursor))
        rows = tables.list_rows(database_id=DB, table_id=table_id, queries=queries).rows
        ids.update(r.id for r in rows)
        if len(rows) < 100:
            return ids
        cursor = rows[-1].id


def sync_table(tables: TablesDB, table_id: str, rows: dict[str, tuple[dict, dict]], overwrite: bool) -> None:
    """rows: {row_id: (данные только для новой строки, данные с сайта)}."""
    existing = all_ids(tables, table_id)
    created = updated = 0
    for row_id, (initial, site) in rows.items():
        if row_id not in existing:
            tables.create_row(database_id=DB, table_id=table_id, row_id=row_id, data={**initial, **site})
            created += 1
        elif overwrite:
            tables.update_row(database_id=DB, table_id=table_id, row_id=row_id, data=site)
            updated += 1
    print(f"  {table_id}: добавлено {created}, обновлено {updated}, без изменений {len(rows) - created - updated}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--overwrite", action="store_true", help="обновить данные с сайта у существующих товаров")
    args = parser.parse_args()
    if not CATALOG_FILE.exists():
        raise SystemExit("Нет data/catalog.json — сначала запустите: python -m scripts.scrape")
    data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    client = make_client(settings)
    tables, storage = TablesDB(client), Storage(client)

    site_products = [p for cat in data["categories"] for p in cat["products"]]
    paths: list[str] = []
    for p in site_products:
        paths += [i for i in p["images"] if i not in paths]
        paths += [v["image"] for v in p["variants"] if v.get("image") and v["image"] not in paths]
    file_ids = upload_images(storage, paths)

    products: dict[str, tuple[dict, dict]] = {}
    variants: dict[str, tuple[dict, dict]] = {}
    for sort, p in enumerate(site_products, 1):
        product_id = short_id("p_", p["slug"])
        initial = {"price": None, "unit": guess_unit(p["name"]), "min_order": 1, "active": True, "sort": sort * 10}
        site = {
            "name": p["name"][:255],
            "description": p["description"][:16000],
            "specs": p.get("specs", "")[:100000],
            "image_ids": [file_ids[i] for i in p["images"] if i in file_ids][:50],
            "url": p["url"][:512],
            "slug": p["slug"][:255],
        }
        products[product_id] = (initial, site)
        for idx, v in enumerate(p["variants"]):
            variant_id = short_id("v_", f"{p['slug']}|{idx}|{v['title']}")
            variants[variant_id] = ({"sort": idx}, {
                "product_id": product_id,
                "title": v["title"][:255],
                "group_name": (v.get("group") or "")[:255],
                "specs": (v.get("specs") or "")[:16000],
                "image_id": file_ids.get(v.get("image") or ""),
            })

    print("Записываю товары в Appwrite ...")
    sync_table(tables, schema.PRODUCTS, products, args.overwrite)
    sync_table(tables, schema.VARIANTS, variants, args.overwrite)
    print(f"Готово: товаров {len(products)}, марок {len(variants)}")


if __name__ == "__main__":
    main()
