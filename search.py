import argparse
import json
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

BOOKS_FILE = Path("douban_books_translated.json")
MOVIES_FILE = Path("douban_movies_translated.json")
GOODREADS_OUT = Path("goodreads_targets.json")
IMDB_OUT = Path("imdb_targets.json")

IMDB_COOKIE = os.getenv("IMDB_COOKIE", "")
GOODREADS_COOKIE = os.getenv("GOODREADS_COOKIE", "")
USER_AGENT = os.getenv(
    "BROWSER_UA",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
)
ACCEPT_LANGUAGE = os.getenv("ACCEPT_LANGUAGE", "en-US,en;q=0.9")


def make_headers(use_cookie: str = "") -> Dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": ACCEPT_LANGUAGE,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    if use_cookie:
        headers["Cookie"] = use_cookie
    return headers


def fetch_html(url: str, headers: Dict[str, str], timeout: int = 10) -> Optional[str]:
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            return resp.text
        print(f"[WARN] {url} -> {resp.status_code}")
    except Exception as e:
        print(f"[WARN] fetch {url} failed: {e}")
    return None


def load_items(path: Path) -> List[Dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def load_mapping(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    mapping: Dict[str, str] = {}
    for row in raw:
        if not isinstance(row, dict):
            continue
        src = row.get("subject_url") or row.get("source")
        dst = row.get("target_url") or row.get("target")
        if src and dst:
            mapping[src] = dst
    return mapping


def save_mapping(path: Path, mapping: Dict[str, str]) -> None:
    rows = [{"subject_url": k, "target_url": v} for k, v in mapping.items()]
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] saved {len(rows)} rows to {path}")


def search_goodreads(title: str, author: str, delay: float) -> Optional[str]:
    q = "+".join([part for part in [title, author] if part]).replace(" ", "+")
    url = f"https://www.goodreads.com/search?q={q}"
    html = fetch_html(url, make_headers(GOODREADS_COOKIE))
    time.sleep(delay)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    a = soup.select_one("a.bookTitle") or soup.select_one(".bookTitle")
    if a and a.get("href"):
        href = a.get("href")
        if href.startswith("/"):
            return "https://www.goodreads.com" + href
        return href
    return None


def search_imdb(title: str, director: str, delay: float) -> Optional[str]:
    q = "+".join([part for part in [title, director] if part]).replace(" ", "+")
    url = f"https://www.imdb.com/find/?q={q}&s=tt&ttype=ft"
    html = fetch_html(url, make_headers(IMDB_COOKIE))
    time.sleep(delay)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    a = soup.select_one("td.result_text a")
    if a and a.get("href"):
        href = a.get("href")
        if href.startswith("/"):
            return "https://www.imdb.com" + href.split("?")[0]
        return href.split("?")[0]
    return None


def build_goodreads_mapping(items: List[Dict], existing: Dict[str, str], delay: float, overwrite: bool) -> Dict[str, str]:
    mapping = dict(existing)
    for item in tqdm(items, desc="Search Goodreads"):
        src = item.get("subject_url")
        if not src:
            continue
        if src in mapping and not overwrite:
            continue
        title = item.get("title") or item.get("title_en") or ""
        author = item.get("author") or ""
        target = search_goodreads(title, author, delay)
        if target:
            mapping[src] = target
    return mapping


def build_imdb_mapping(items: List[Dict], existing: Dict[str, str], delay: float, overwrite: bool) -> Dict[str, str]:
    mapping = dict(existing)
    for item in tqdm(items, desc="Search IMDb"):
        src = item.get("subject_url")
        if not src:
            continue
        if src in mapping and not overwrite:
            continue
        title = item.get("title") or item.get("title_en") or ""
        director = item.get("director") or ""
        target = search_imdb(title, director, delay)
        if target:
            mapping[src] = target
    return mapping


def main():
    parser = argparse.ArgumentParser(description="自动搜索 Goodreads/IMDb 生成映射文件")
    parser.add_argument("--delay", type=float, default=1.5, help="请求间隔秒")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已有映射")
    args = parser.parse_args()

    books = load_items(BOOKS_FILE)
    movies = load_items(MOVIES_FILE)

    gr_map = load_mapping(GOODREADS_OUT)
    imdb_map = load_mapping(IMDB_OUT)

    gr_map = build_goodreads_mapping(books, gr_map, args.delay, args.overwrite)
    imdb_map = build_imdb_mapping(movies, imdb_map, args.delay, args.overwrite)

    save_mapping(GOODREADS_OUT, gr_map)
    save_mapping(IMDB_OUT, imdb_map)


if __name__ == "__main__":
    main()
