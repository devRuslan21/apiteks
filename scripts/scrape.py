"""Парсер каталога apiteks.uz.

Собирает разделы, товары (страницы категорий), позиции (строки таблиц марок)
и скачивает все картинки товаров в data/images. Результат: data/catalog.json.

Запуск:  python -m scripts.scrape
"""
from __future__ import annotations

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

BASE = "https://apiteks.uz"
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
IMAGES_DIR = DATA_DIR / "images"
CATALOG_FILE = DATA_DIR / "catalog.json"

OTHER_CATEGORY = "Прочая продукция"
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")
THUMB_RE = re.compile(r"/thumb/\d+/[^/]+/[^/]+/d/(?P<file>[^?#]+)")
SKIP_COLUMNS = re.compile(r"^(№|n|фото|изображение|картинка)$", re.I)
NAME_COLUMN_PRIORITY = [
    r"наименование|название",
    r"марка",
    r"модель",
    r"тип",
    r"артикул",
    r"диаметр",
]
# Служебные страницы сайта, которые не являются товарами.
NON_PRODUCT_PATHS = {
    "/", "/about", "/kontakty", "/katalog", "/products", "/novostnaya_lenta", "/novosti",
    "/sotrudnichestvo", "/uslugi", "/obratnaya_svaz", "/partnyori", "/potrebnosti", "/gost",
    "/vakansii-1", "/search", "/partnerskie-i-gosudarstvennye-programmy-1", "/users",
}

client = httpx.Client(
    timeout=40,
    follow_redirects=True,
    headers={"User-Agent": "Mozilla/5.0 (apiteks-telegram-bot catalog sync)"},
)


def log(*args) -> None:
    print(*args, flush=True)


def fetch(url: str, retries: int = 3) -> httpx.Response:
    for attempt in range(1, retries + 1):
        try:
            resp = client.get(url)
            resp.raise_for_status()
            return resp
        except httpx.HTTPError:
            if attempt == retries:
                raise
            time.sleep(1.5 * attempt)
    raise RuntimeError("unreachable")


def soup_of(url: str) -> BeautifulSoup:
    return BeautifulSoup(fetch(url).text, "lxml")


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def page_path(href: str) -> str | None:
    """Нормализует ссылку на внутреннюю страницу сайта в путь вида /slug."""
    url = urljoin(BASE + "/", href.strip())
    parsed = urlparse(url)
    if parsed.netloc and parsed.netloc.replace("www.", "") != "apiteks.uz":
        return None
    path = parsed.path.rstrip("/") or "/"
    if path.startswith(("/f/", "/d/", "/thumb/", "/g/", "/t/")) or path.lower().endswith(IMAGE_EXT + (".pdf",)):
        return None
    return path


def original_image_url(img: Tag) -> str | None:
    candidates = []
    link = img.find_parent("a")
    if link and link.get("href"):
        candidates.append(link["href"])
    candidates += [img.get("data-src"), img.get("src")]
    for cand in candidates:
        if not cand or "spacer.gif" in cand:
            continue
        url = urljoin(BASE + "/", cand.strip())
        m = THUMB_RE.search(urlparse(url).path)
        if m:
            return f"{BASE}/d/{m.group('file')}"
        path = urlparse(url).path.lower()
        if path.endswith(IMAGE_EXT):
            return url
    return None


def expand_table(table: Tag) -> list[tuple[bool, list[Tag | None]]]:
    """Разворачивает таблицу с rowspan/colspan в сетку.

    Возвращает список строк (is_header, cells), где cells — ячейки по колонкам.
    """
    grid: list[tuple[bool, list[Tag | None]]] = []
    carry: dict[int, tuple[Tag, int]] = {}
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"], recursive=False)
        row: list[Tag | None] = []
        col = 0
        it = iter(cells)
        pending = next(it, None)
        while pending is not None or any(c >= col for c in carry):
            if col in carry:
                cell, left = carry[col]
                row.append(cell)
                if left > 1:
                    carry[col] = (cell, left - 1)
                else:
                    del carry[col]
                col += 1
                continue
            if pending is None:
                col += 1
                if col > 60:
                    break
                row.append(None)
                continue
            colspan = int(pending.get("colspan", 1) or 1)
            rowspan = int(pending.get("rowspan", 1) or 1)
            for _ in range(colspan):
                row.append(pending)
                if rowspan > 1:
                    carry[col] = (pending, rowspan - 1)
                col += 1
            pending = next(it, None)
        while row and row[-1] is None:
            row.pop()
        is_header = bool(cells) and (
            all(c.name == "th" for c in cells) or (tr.parent is not None and tr.parent.name == "thead")
        )
        grid.append((is_header, row))
    return grid


def table_headers(grid) -> tuple[list[str], list[list[Tag | None]]]:
    header_rows = [r for is_h, r in grid if is_h]
    data_rows = [r for is_h, r in grid if not is_h]
    if not header_rows and len(data_rows) > 1:
        header_rows, data_rows = [data_rows[0]], data_rows[1:]
    width = max((len(r) for r in header_rows + data_rows), default=0)
    headers = []
    for col in range(width):
        parts: list[str] = []
        for hr in header_rows:
            cell = hr[col] if col < len(hr) else None
            text = clean(cell.get_text(" ")) if cell is not None else ""
            if text and text not in parts:
                parts.append(text)
        headers.append(" / ".join(parts) or f"Колонка {col + 1}")
    return headers, data_rows


def pick_name_column(headers: list[str]) -> int:
    for pattern in NAME_COLUMN_PRIORITY:
        for i, h in enumerate(headers):
            if re.search(pattern, h, re.I):
                return i
    for i, h in enumerate(headers):
        if not SKIP_COLUMNS.match(h.strip()):
            return i
    return 0


def is_inside(tag: Tag, ancestor: Tag) -> bool:
    return any(p is ancestor for p in tag.parents)


def section_title(table: Tag, body: Tag) -> str:
    prev = table.find_previous(
        lambda t: isinstance(t, Tag)
        and (t.name in ("h2", "h3", "h4") or "table-section-title" in (t.get("class") or []))
    )
    if prev is None or not is_inside(prev, body):
        return ""
    return clean(prev.get_text(" "))[:200]


def is_spec_table(headers: list[str]) -> bool:
    """Таблица характеристик («Параметр — Значение» или колонки-размеры), а не список марок."""
    if re.search(r"параметр|показател|характеристик|свойств", headers[0], re.I):
        return True
    if any(re.match(r"\s*(значение|норма|величина)", h, re.I) for h in headers[1:]):
        return True
    rest = headers[1:]
    numeric = sum(1 for h in rest if re.match(r"\s*[\d.,]", h))
    return len(rest) >= 2 and numeric / len(rest) >= 0.6


def spec_lines(headers: list[str], rows: list[list[Tag | None]]) -> list[str]:
    lines = []
    for row in rows:
        texts = [clean(c.get_text(" ")) if c is not None else "" for c in row]
        if not texts or not texts[0]:
            continue
        values = [
            v if len(headers) == 2 or re.match(r"\s*значение", h, re.I) else f"{h}: {v}"
            for h, v in zip(headers[1:], texts[1:])
            if v and v not in ("—", "-")
        ]
        if values:
            lines.append(f"• {texts[0]}: {'; '.join(values)}")
    return lines


def parse_tables(body: Tag) -> tuple[list[dict], str]:
    """Возвращает позиции (марки) и текст характеристик товара."""
    variants: list[dict] = []
    specs_text: list[str] = []
    seen: set[str] = set()
    variant_tables = 0
    for table in body.find_all("table"):
        outer = table.find_parent("table")
        if outer is not None and is_inside(outer, body):
            continue
        grid = expand_table(table)
        headers, rows = table_headers(grid)
        if len(headers) < 2 or not rows:
            continue
        if is_spec_table(headers):
            title = section_title(table, body)
            lines = spec_lines(headers, rows)
            if lines:
                specs_text.append("\n".join(([title] if title else []) + lines))
            continue
        variant_tables += 1
        name_col = pick_name_column(headers)
        color_col = next((i for i, h in enumerate(headers) if re.search(r"цвет", h, re.I)), None)
        art_col = next((i for i, h in enumerate(headers) if re.search(r"артикул", h, re.I)), None)
        group = section_title(table, body)
        for row in rows:
            texts = [clean(c.get_text(" ")) if c is not None else "" for c in row]
            texts += [""] * (len(headers) - len(texts))
            name = texts[name_col] if name_col < len(texts) else ""
            if not name or name in ("—", "-"):
                continue
            title = name
            for extra_col in (art_col, color_col):
                if extra_col is not None and extra_col != name_col:
                    extra = texts[extra_col]
                    if extra and extra not in ("—", "-") and extra not in title:
                        title = f"{title}, {extra}"
            specs = []
            for i, (h, v) in enumerate(zip(headers, texts)):
                if i == name_col or not v or v in ("—", "-") or SKIP_COLUMNS.match(h.strip()):
                    continue
                specs.append(f"{h}: {v}")
            spec_text = "\n".join(specs)
            key = f"{title}|{spec_text}"
            if key in seen:
                continue
            seen.add(key)
            image = None
            for cell in row:
                if cell is not None and (img := cell.find("img")) is not None:
                    image = original_image_url(img)
                    if image:
                        break
            variants.append({
                "title": title[:250],
                "group": group,
                "specs": spec_text[:8000],
                "image_url": image,
            })
    if variant_tables < 2:
        for v in variants:
            v["group"] = ""
    return variants, "\n\n".join(specs_text)[:20000]


def parse_description(body: Tag) -> str:
    body = BeautifulSoup(str(body), "lxml")
    for t in body.find_all(["table", "style", "script", "h1", "noscript", "form"]):
        t.decompose()
    lines: list[str] = []
    for el in body.find_all(["h2", "h3", "h4", "p", "li"]):
        if el.find(["p", "li"]):
            continue
        text = clean(el.get_text(" "))
        if not text or text in ("Подробнее →", "Подробнее"):
            continue
        if el.name == "li":
            text = f"• {text}"
        if not lines or lines[-1] != text:
            lines.append(text)
    return "\n".join(lines)[:10000]


def parse_catalog_index() -> tuple[list[dict], dict[str, str]]:
    """Возвращает разделы с путями товаров и обложки товаров со страницы /products."""
    categories: list[dict] = []
    soup = soup_of(f"{BASE}/katalog")
    for card in soup.select(".cat-card"):
        title = card.select_one(".cat-title")
        if title is None:
            continue
        paths = []
        for a in card.select(".cat-list a[href]"):
            path = page_path(a["href"])
            if path and path not in NON_PRODUCT_PATHS and path not in paths:
                paths.append(path)
        categories.append({"name": clean(title.get_text(" ")).capitalize(), "paths": paths})

    covers: dict[str, str] = {}
    known = {p for c in categories for p in c["paths"]}
    extra: list[str] = []
    body = soup_of(f"{BASE}/products").select_one("div.bodycontent")
    for a in body.select("a[href]") if body else []:
        path = page_path(a["href"])
        if not path or path in NON_PRODUCT_PATHS:
            continue
        img = a.find("img")
        if img is not None and (url := original_image_url(img)):
            covers.setdefault(path, url)
        if path not in known and path not in extra:
            extra.append(path)
    if extra:
        categories.append({"name": OTHER_CATEGORY, "paths": extra})
    return categories, covers


def scrape_page(path: str) -> tuple[dict | None, list[str]]:
    url = BASE + path
    try:
        soup = soup_of(url)
    except httpx.HTTPError as exc:
        log(f"  ! {path}: {exc}")
        return None, []
    body = soup.select_one("div.bodycontent")
    if body is None:
        return None, []
    h1 = body.find("h1")
    name = clean(h1.get_text(" ")) if h1 else path.strip("/")
    images: list[str] = []
    for img in body.find_all("img"):
        src = original_image_url(img)
        if src and src not in images:
            images.append(src)
    variants, specs = parse_tables(body)
    for v in variants:
        if v["image_url"] and v["image_url"] not in images:
            images.append(v["image_url"])
    children = []
    for a in body.find_all("a", href=True):
        child = page_path(a["href"])
        if child and child != path and child not in NON_PRODUCT_PATHS and not child.startswith("/novosti"):
            children.append(child)
    product = {
        "slug": path.strip("/"),
        "name": name,
        "url": url,
        "description": parse_description(body),
        "specs": specs,
        "image_urls": images,
        "variants": variants,
    }
    return product, children


def safe_filename(url: str) -> str:
    name = unquote(Path(urlparse(url).path).name)
    name = re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE).strip("._") or "image.jpg"
    if not name.lower().endswith(IMAGE_EXT):
        name += ".jpg"
    return name[-120:]


def download_images(products: list[dict]) -> None:
    jobs: dict[str, Path] = {}
    for product in products:
        folder = IMAGES_DIR / product["slug"]
        for idx, url in enumerate(product["image_urls"], 1):
            if url not in jobs:
                jobs[url] = folder / f"{idx:02d}_{safe_filename(url)}"

    def worker(item: tuple[str, Path]) -> tuple[str, str | None]:
        url, dest = item
        if dest.exists() and dest.stat().st_size > 0:
            return url, str(dest)
        try:
            resp = fetch(url)
        except httpx.HTTPError as exc:
            log(f"  ! картинка не скачана {url}: {exc}")
            return url, None
        if not resp.headers.get("content-type", "").startswith("image/"):
            log(f"  ! не картинка {url}")
            return url, None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return url, str(dest)

    log(f"Скачиваю картинки: {len(jobs)} шт.")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = dict(pool.map(worker, jobs.items()))

    def rel(p: str | None) -> str | None:
        return Path(p).relative_to(ROOT).as_posix() if p else None

    for product in products:
        product["images"] = [rel(results[u]) for u in product["image_urls"] if results.get(u)]
        for v in product["variants"]:
            v["image"] = rel(results.get(v["image_url"])) if v["image_url"] else None
    ok = sum(1 for p in results.values() if p)
    log(f"Скачано картинок: {ok} из {len(jobs)}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    log("Читаю каталог apiteks.uz ...")
    index, covers = parse_catalog_index()
    seen: set[str] = set()
    categories: list[dict] = []
    all_products: list[dict] = []
    for sort, cat in enumerate(index, 1):
        queue = list(cat["paths"])
        products: list[dict] = []
        depth = {p: 0 for p in queue}
        while queue:
            path = queue.pop(0)
            if path in seen:
                continue
            seen.add(path)
            product, children = scrape_page(path)
            time.sleep(0.3)
            if product is None:
                continue
            # Хаб-страница без собственных позиций и фото — только раскрываем дочерние страницы.
            is_hub = not product["variants"] and not product["image_urls"] and bool(children)
            if path in covers and covers[path] not in product["image_urls"]:
                product["image_urls"].insert(0, covers[path])
            new_children = [c for c in children if c not in seen and c not in depth]
            if depth[path] < 2:
                for child in new_children:
                    depth[child] = depth[path] + 1
                    queue.append(child)
            if is_hub:
                log(f"  ~ {path}: раздел-хаб, дочерних страниц {len(new_children)}")
                continue
            log(f"  + {product['name']}: позиций {len(product['variants'])}, фото {len(product['image_urls'])}")
            products.append(product)
        if products:
            categories.append({"slug": f"cat-{sort}", "name": cat["name"], "sort": sort, "products": products})
            all_products += products

    download_images(all_products)
    for product in all_products:
        product.pop("image_urls", None)
        for v in product["variants"]:
            v.pop("image_url", None)

    DATA_DIR.mkdir(exist_ok=True)
    CATALOG_FILE.write_text(
        json.dumps(
            {"source": BASE, "scraped_at": datetime.now(timezone.utc).isoformat(), "categories": categories},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    n_var = sum(len(p["variants"]) for p in all_products)
    log(f"Готово: разделов {len(categories)}, товаров {len(all_products)}, позиций {n_var} -> {CATALOG_FILE}")


if __name__ == "__main__":
    main()
