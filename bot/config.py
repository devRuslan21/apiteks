import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _int_set(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.replace(";", ",").split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: frozenset[int]
    orders_chat_id: int | None
    appwrite_endpoint: str
    appwrite_project_id: str
    appwrite_api_key: str
    appwrite_self_signed: bool
    catalog_ttl_seconds: int
    currency: str

    @classmethod
    def load(cls) -> "Settings":
        chat = os.getenv("ORDERS_CHAT_ID", "").strip()
        return cls(
            bot_token=os.getenv("BOT_TOKEN", "").strip(),
            admin_ids=_int_set(os.getenv("ADMIN_IDS", "")),
            orders_chat_id=int(chat) if chat else None,
            appwrite_endpoint=os.getenv("APPWRITE_ENDPOINT", "http://localhost/v1").strip(),
            appwrite_project_id=os.getenv("APPWRITE_PROJECT_ID", "").strip(),
            appwrite_api_key=os.getenv("APPWRITE_API_KEY", "").strip(),
            appwrite_self_signed=os.getenv("APPWRITE_SELF_SIGNED", "true").lower() in ("1", "true", "yes"),
            catalog_ttl_seconds=int(os.getenv("CATALOG_TTL_SECONDS", "60")),
            currency=os.getenv("CURRENCY", "сум").strip(),
        )

    def require_appwrite(self) -> None:
        missing = [
            name
            for name, value in (
                ("APPWRITE_ENDPOINT", self.appwrite_endpoint),
                ("APPWRITE_PROJECT_ID", self.appwrite_project_id),
                ("APPWRITE_API_KEY", self.appwrite_api_key),
            )
            if not value
        ]
        if missing:
            raise SystemExit(f"Заполните в .env: {', '.join(missing)}")


settings = Settings.load()
