"""Работа с Appwrite: товары, пользователи, заявки, картинки.

SDK Appwrite синхронный, поэтому каждый вызов уходит в отдельный поток.
"""
from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

import urllib3
from appwrite.client import Client
from appwrite.enums.image_format import ImageFormat
from appwrite.exception import AppwriteException
from appwrite.id import ID
from appwrite.query import Query
from appwrite.services.storage import Storage
from appwrite.services.tables_db import TablesDB

from . import schema
from .config import Settings


def make_client(settings: Settings) -> Client:
    settings.require_appwrite()
    client = (
        Client()
        .set_endpoint(settings.appwrite_endpoint)
        .set_project(settings.appwrite_project_id)
        .set_key(settings.appwrite_api_key)
    )
    if settings.appwrite_self_signed:
        client.set_self_signed(True)
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return client


def row_to_dict(row) -> dict[str, Any]:
    return {
        "$id": row.id,
        "$sequence": row.sequence,
        "$createdAt": row.createdat,
        **(row.data or {}),
    }


class Database:
    def __init__(self, settings: Settings) -> None:
        client = make_client(settings)
        self.tables = TablesDB(client)
        self.storage = Storage(client)
        self.db_id = schema.DATABASE_ID

    async def _run(self, fn, /, *args, **kwargs):
        return await asyncio.to_thread(partial(fn, *args, **kwargs))

    async def _list(self, table: str, queries: list[str] | None = None) -> list[dict]:
        result = await self._run(self.tables.list_rows, database_id=self.db_id, table_id=table, queries=queries or [])
        return [row_to_dict(r) for r in result.rows]

    async def list_all(self, table: str, queries: list[str] | None = None) -> list[dict]:
        rows: list[dict] = []
        cursor: str | None = None
        while True:
            page_q = list(queries or []) + [Query.limit(100)]
            if cursor:
                page_q.append(Query.cursor_after(cursor))
            page = await self._list(table, page_q)
            rows += page
            if len(page) < 100:
                return rows
            cursor = page[-1]["$id"]

    async def _get(self, table: str, row_id: str) -> dict | None:
        try:
            row = await self._run(self.tables.get_row, database_id=self.db_id, table_id=table, row_id=row_id)
        except AppwriteException as exc:
            if exc.code == 404:
                return None
            raise
        return row_to_dict(row)

    async def _create(self, table: str, data: dict, row_id: str | None = None) -> dict:
        row = await self._run(
            self.tables.create_row,
            database_id=self.db_id,
            table_id=table,
            row_id=row_id or ID.unique(),
            data=data,
        )
        return row_to_dict(row)

    async def _update(self, table: str, row_id: str, data: dict) -> dict:
        row = await self._run(self.tables.update_row, database_id=self.db_id, table_id=table, row_id=row_id, data=data)
        return row_to_dict(row)

    # ---------- товары ----------
    async def load_catalog(self) -> tuple[list[dict], list[dict]]:
        products, variants = await asyncio.gather(
            self.list_all(schema.PRODUCTS, [Query.order_asc("sort")]),
            self.list_all(schema.VARIANTS, [Query.order_asc("sort")]),
        )
        return products, variants

    async def image_bytes(self, file_id: str) -> bytes:
        """Картинка в JPEG (webp/png конвертирует сам Appwrite)."""
        try:
            return await self._run(
                self.storage.get_file_preview,
                bucket_id=schema.BUCKET_ID,
                file_id=file_id,
                width=1280,
                quality=85,
                output=ImageFormat.JPG,
            )
        except AppwriteException:
            return await self._run(self.storage.get_file_view, bucket_id=schema.BUCKET_ID, file_id=file_id)

    # ---------- пользователи ----------
    async def get_user(self, tg_id: int) -> dict | None:
        return await self._get(schema.USERS, str(tg_id))

    async def upsert_user(self, tg_id: int, username: str | None, full_name: str, **extra) -> dict:
        data = {"tg_id": tg_id, "username": (username or "")[:64], "full_name": full_name[:255], **extra}
        if await self.get_user(tg_id) is None:
            return await self._create(schema.USERS, data, row_id=str(tg_id))
        return await self._update(schema.USERS, str(tg_id), data)

    # ---------- заявки ----------
    async def create_order(self, data: dict) -> dict:
        return await self._create(schema.ORDERS, {"status": "new", **data})

    async def get_order(self, order_id: str) -> dict | None:
        return await self._get(schema.ORDERS, order_id)

    async def user_orders(self, tg_id: int, limit: int = 10) -> list[dict]:
        return await self._list(schema.ORDERS, [
            Query.equal("user_id", str(tg_id)),
            Query.order_desc("$createdAt"),
            Query.limit(limit),
        ])

    async def recent_orders(self, status: str | None = None, limit: int = 15) -> list[dict]:
        queries = [Query.order_desc("$createdAt"), Query.limit(limit)]
        if status:
            queries.insert(0, Query.equal("status", status))
        return await self._list(schema.ORDERS, queries)

    async def set_order_status(self, order_id: str, status: str) -> dict:
        return await self._update(schema.ORDERS, order_id, {"status": status})
