"""抓取豆瓣书/电影列表，生成 books、movies 及合并 JSON。"""

import json
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm


DOUBAN_COOKIE = os.getenv("DOUBAN_COOKIE", "YOUR_DOUBAN_COOKIE_HERE")
USER_ID = os.getenv("DOUBAN_USER_ID", "YOUR_DOUBAN_USER_ID")

BOOK_RAW_FILE = "douban_books_raw.json"
MOVIE_RAW_FILE = "douban_movies_raw.json"
ALL_RAW_FILE = "douban_export_raw.json"
DIRECTOR_DELAY = float(os.getenv("DIRECTOR_DELAY", "0.3"))


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Cookie": DOUBAN_COOKIE,
    })
    return s


def fetch_html(session: requests.Session, url: str) -> Optional[str]:
    try:
        resp = session.get(url, timeout=10)
        if resp.status_code == 200:
            return resp.text
        print(f"[WARN] {url} -> status {resp.status_code}")
        return None
    except Exception as e:
        print(f"[ERROR] Failed to fetch {url}: {e}")
        return None


def extract_rating_from_classes(classes: List[str]) -> Optional[int]:
    """从 class 名里提取 ratingX-t 形式的分数。"""
    for c in classes:
        m = re.search(r"rating(\d+)-t", c)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
    return None


def split_people(text: str) -> List[str]:
    if not text:
        return []
    return [p.strip() for p in re.split(r"[、/,，；;]+", text) if p.strip()]


def extract_directors_from_detail(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    directors = [a.get_text(strip=True) for a in soup.select('a[rel="v:directedBy"]')]
    if directors:
        return directors
    info_div = soup.select_one("#info")
    if info_div:
        info_text = info_div.get_text(" ", strip=True)
        # 先找“导演:”标签
        m = re.search(r"(导演|Director(?:s)?)[:：]\s*", info_text, flags=re.IGNORECASE)
        if m:
            rest = info_text[m.end():]
            stop_tokens = [
                "主演", "演员", "类型", "片长", "又名", "首播", "上映", "语言", "编剧",
                "国家", "地区", "季数", "集数", "Starring", "Cast",
            ]
            stop_idx = len(rest)
            for tok in stop_tokens:
                idx = rest.find(tok)
                if idx != -1 and idx < stop_idx:
                    stop_idx = idx
            segment = rest[:stop_idx]
            segment = segment.split("/", 1)[0]
            dirs = split_people(segment)
            if dirs:
                return dirs
    return []


def parse_book_collect_page(html: str) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    result = []

    items = soup.select(".subject-item")
    for it in items:
        a_tag = it.select_one("h2 a")
        if not a_tag:
            continue
        title = a_tag.get_text(strip=True)
        href = a_tag.get("href", "").strip()

        rating_span = it.select_one('[class*="rating"]')
        rating = None
        if rating_span:
            rating = extract_rating_from_classes(rating_span.get("class", []))
        if rating is None:
            rating = extract_rating_from_classes(it.get("class", []))

        comment_span = it.select_one(".comment")
        comment = comment_span.get_text(strip=True) if comment_span else ""

        pub_span = it.select_one(".pub")
        authors = []
        if pub_span:
            pub_text = pub_span.get_text(" ", strip=True)
            authors_text = pub_text.split("/", 1)[0]
            authors = split_people(authors_text)

        date_span = it.select_one(".pubtime") or it.select_one(".date")
        date_text = ""
        if date_span:
            raw_date = date_span.get_text(strip=True)
            date_text = raw_date.split()[0]  # 取空格前的日期部分

        result.append({
            "category": "book",
            "title_zh": title,
            "subject_url": href,
            "rating": rating,
            "comment_zh": comment,
            "comment_date": date_text,
            "authors_zh": authors,
        })

    return result


def parse_movie_collect_page(html: str) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    result = []

    items = soup.select(".item")
    for it in items:
        a_tag = it.select_one("li.title a") or it.select_one("a.nbg") or it.select_one("a")
        if not a_tag:
            continue
        title = a_tag.get_text(strip=True)
        href = a_tag.get("href", "").strip()

        rating_span = it.select_one('[class*="rating"]')
        rating = None
        if rating_span:
            rating = extract_rating_from_classes(rating_span.get("class", []))
        if rating is None:
            rating = extract_rating_from_classes(it.get("class", []))

        comment_span = it.select_one(".comment")
        comment = comment_span.get_text(strip=True) if comment_span else ""

        date_span = it.select_one(".date")
        date_text = date_span.get_text(strip=True) if date_span else ""

        result.append({
            "category": "movie",
            "title_zh": title,
            "subject_url": href,
            "rating": rating,
            "comment_zh": comment,
            "comment_date": date_text,
            "directors_zh": [],
        })

    return result


def crawl_collect_list(
    session: requests.Session,
    base_url: str,
    parser,
    max_pages: int = 999,
    per_page: int = 15,
    delay: float = 1.5,
) -> List[Dict]:
    all_items: List[Dict] = []
    for page_idx in range(max_pages):
        start = page_idx * per_page
        url = f"{base_url}?start={start}&sort=time&rating=all&filter=all&mode=grid"
        html = fetch_html(session, url)
        if not html:
            break

        items = parser(html)
        if not items:
            break

        all_items.extend(items)
        print(f"[INFO] {base_url} page {page_idx+1}: {len(items)} items")
        time.sleep(delay)

    return all_items


def enrich_movie_directors(session: requests.Session, movies: List[Dict]) -> List[Dict]:
    """对每部电影访问详情页，获取导演。"""
    if not movies:
        return movies
    for item in tqdm(movies, desc="Fetching directors from detail"):
        url = item.get("subject_url")
        if not url:
            continue
        html = fetch_html(session, url)
        if not html:
            continue
        dirs = extract_directors_from_detail(html)
        item["directors_zh"] = dirs
        time.sleep(DIRECTOR_DELAY)
    return movies


def crawl_all() -> Dict[str, List[Dict]]:
    s = make_session()
    book_url = f"https://book.douban.com/people/{USER_ID}/collect"
    movie_url = f"https://movie.douban.com/people/{USER_ID}/collect"

    print("=== Crawl books ===")
    books = crawl_collect_list(s, book_url, parse_book_collect_page)
    print("=== Crawl movies ===")
    movies = crawl_collect_list(s, movie_url, parse_movie_collect_page)
    movies = enrich_movie_directors(s, movies)
    musics: List[Dict] = []

    print(
        f"[SUMMARY] books={len(books)}, movies={len(movies)}, "
        f"musics={len(musics)}, total={len(books) + len(movies) + len(musics)}"
    )

    return {"book": books, "movie": movies, "music": musics}


def save_json(path: Path, data: List[Dict]) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[INFO] saved {len(data)} items to {path}")


def main():
    if not DOUBAN_COOKIE or "YOUR_DOUBAN_COOKIE_HERE" in DOUBAN_COOKIE:
        raise ValueError("请设置 DOUBAN_COOKIE（可用环境变量或直接改本文件）。")
    if USER_ID == "YOUR_DOUBAN_USER_ID":
        raise ValueError("请把 USER_ID 改成你的豆瓣 people ID，或设置环境变量 DOUBAN_USER_ID。")

    data = crawl_all()
    save_json(Path(BOOK_RAW_FILE), data["book"])
    save_json(Path(MOVIE_RAW_FILE), data["movie"])
    all_items = data["book"] + data["movie"]
    save_json(Path(ALL_RAW_FILE), all_items)


if __name__ == "__main__":
    main()
