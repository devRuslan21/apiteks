"""Кэш товаров из Appwrite. Перечитывается не чаще раза в CATALOG_TTL_SECONDS,
поэтому правки в консоли Appwrite появляются в боте примерно через минуту."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from .db import Database

log = logging.getLogger(__name__)


@dataclass
class Variant:
    id: str
    product_id: str
    title: str
    group: str
    specs: str
    image_id: str | None


@dataclass
class Product:
    id: str
    name: str
    description: str
    price: float | None
    unit: str
    min_order: float
    image_ids: list[str]
    specs: str
    url: str
    variants: list[Variant] = field(default_factory=list)


def _num(value, default):
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


class Catalog:
    def __init__(self) -> None:
        self.products: list[Product] = []
        self.product_by_id: dict[str, Product] = {}
        self.variant_by_id: dict[str, Variant] = {}
        self.loaded_at = 0.0
        self._lock = asyncio.Lock()

    async def load(self, db: Database) -> None:
        product_rows, variant_rows = await db.load_catalog()
        products: list[Product] = []
        for r in product_rows:
            if r.get("active") is False:
                continue
            min_order = _num(r.get("min_order"), 1.0)
            products.append(Product(
                id=r["$id"],
                name=r["name"],
                description=r.get("description") or "",
                price=_num(r.get("price"), None),
                unit=(r.get("unit") or "шт").strip(),
                min_order=min_order if min_order > 0 else 1.0,
                image_ids=[i for i in (r.get("image_ids") or []) if i],
                specs=r.get("specs") or "",
                url=r.get("url") or "",
            ))
        product_by_id = {p.id: p for p in products}
        variant_by_id: dict[str, Variant] = {}
        for r in variant_rows:
            variant = Variant(
                id=r["$id"],
                product_id=r["product_id"],
                title=r["title"],
                group=r.get("group_name") or "",
                specs=r.get("specs") or "",
                image_id=r.get("image_id") or None,
            )
            if variant.product_id in product_by_id:
                product_by_id[variant.product_id].variants.append(variant)
                variant_by_id[variant.id] = variant
        self.products, self.product_by_id, self.variant_by_id = products, product_by_id, variant_by_id
        self.loaded_at = time.monotonic()
        log.info("Товары загружены из Appwrite: %d, марок %d", len(products), len(variant_by_id))

    async def refresh(self, db: Database, ttl: float) -> None:
        if time.monotonic() - self.loaded_at < ttl:
            return
        async with self._lock:
            if time.monotonic() - self.loaded_at < ttl:
                return
            try:
                await self.load(db)
            except Exception:
                if not self.products:
                    raise
                log.exception("Не удалось обновить товары, показываю сохранённые")
                self.loaded_at = time.monotonic()

    def index_of(self, product_id: str) -> int:
        for i, p in enumerate(self.products):
            if p.id == product_id:
                return i
        return 0


catalog = Catalog()
