"""把翻译后的书评/影评发布到 Goodreads/IMDb。"""

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from playwright.sync_api import Playwright, sync_playwright, TimeoutError as PlaywrightTimeoutError

# 数据文件
BOOKS_FILE = Path("douban_books_translated.json")
MOVIES_FILE = Path("douban_movies_translated.json")

# 映射文件：豆瓣 subject_url -> 目标站点 URL
GOODREADS_MAPPING_FILE = Path("goodreads_targets.json")
IMDB_MAPPING_FILE = Path("imdb_targets.json")

# Playwright 用户目录（复用登录态）
GOODREADS_PROFILE_DIR = Path(".pw_goodreads_profile")
IMDB_PROFILE_DIR = Path(".pw_imdb_profile")

HEADLESS = os.getenv("HEADLESS", "0") == "1"
WAIT_FOR_LOGIN = os.getenv("WAIT_FOR_LOGIN", "1") == "1"


def load_list_json(path: Path) -> List[Dict]:
    if not path.exists():
        print(f"[WARN] {path} not found, skip.")
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} 内容不是列表")
    return data


def load_mapping(path: Path) -> Dict[str, str]:
    if not path.exists():
        print(f"[WARN] {path} not found, skip.")
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    mapping: Dict[str, str] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        src = entry.get("subject_url") or entry.get("source")
        dst = entry.get("target_url") or entry.get("target")
        if src and dst:
            mapping[src] = dst
    return mapping


def get_target_url(mapping: Dict[str, str], item: Dict) -> Optional[str]:
    subject_url = item.get("subject_url")
    if subject_url and subject_url in mapping:
        return mapping[subject_url]
    return None


def prompt_login(page, home_url: str, label: str) -> None:
    if not WAIT_FOR_LOGIN:
        return
    try:
        page.goto(home_url, wait_until="domcontentloaded")
    except Exception:
        pass
    input(f"[{label}] 请在弹出的浏览器中登录后按回车继续...")


def convert_rating_for_goodreads(rating: Optional[int]) -> Optional[int]:
    if rating is None:
        return None
    rating = max(1, min(5, rating))
    return rating


def convert_rating_for_imdb(rating: Optional[int]) -> Optional[int]:
    if rating is None:
        return None
    imdb_rating = max(1, min(5, rating)) * 2  # Douban 1-5 -> IMDb 2-10
    return imdb_rating


def click_first_available(page, selectors: List[str]) -> bool:
    for sel in selectors:
        try:
            page.click(sel, timeout=3000)
            return True
        except Exception:
            continue
    return False


def fill_first_available(page, selectors: List[str], text: str) -> bool:
    for sel in selectors:
        try:
            page.fill(sel, text, timeout=3000)
            return True
        except Exception:
            continue
    return False


def open_goodreads_editor(page, target_url: str) -> None:
    page.goto(target_url, wait_until="domcontentloaded")
    click_first_available(
        page,
        [
            "a[href*='/review/new']",
            "a.writeReviewLink",
            "button[data-analytics-id='new_review']",
            "a[data-analytics-id='new_review']",
        ],
    )


def open_imdb_editor(page, target_url: str) -> None:
    page.goto(target_url, wait_until="domcontentloaded")
    click_first_available(
        page,
        [
            "a[href*='/review/create']",
            "a[href*='/reviews/write']",
            "a.ipc-button",
        ],
    )


def post_goodreads_review(page, target_url: str, rating: Optional[int], comment: str) -> None:
    open_goodreads_editor(page, target_url)
    if rating:
        click_first_available(
            page,
            [
                f"#rating_star_{rating}",
                f'input[name=\"rating\"][value=\"{rating}\"]',
                f'input[name=\"review[rating]\"][value=\"{rating}\"]',
                f'label[for=\"review_rating_{rating}\"]',
            ],
        )
    filled = fill_first_available(
        page,
        [
            "#review_review_text",
            "textarea[name='review[review]']",
            "textarea#review_text",
            "textarea[id*='review']",
        ],
        comment,
    )
    if not filled:
        raise RuntimeError("找不到 Goodreads 评论输入框，需手动调整选择器")
    if not click_first_available(
        page,
        [
            "#review_submit",
            "input[type='submit'][value*='Save']",
            "button[type='submit']",
            "input[name='commit']",
            "button[name='commit']",
        ],
    ):
        raise RuntimeError("找不到 Goodreads 提交按钮，需手动调整选择器")
    page.wait_for_timeout(2000)


def post_imdb_review(page, target_url: str, rating: Optional[int], comment: str) -> None:
    page.goto(target_url, wait_until="domcontentloaded")
    # IMDb 用户影评页面通常有评分星星和 textarea
    if rating:
        # IMDb 星星可能是按钮或 input，尝试常见选择器
        click_first_available(
            page,
            [f"button[aria-label='{rating}']", f"input[name='rating'][value='{rating}']", "span.star-rating-icon"],
        )
    filled = fill_first_available(page, ["textarea", "textarea[name*='review']"], comment)
    if not filled:
        raise RuntimeError("找不到 IMDb 评论输入框，需手动调整选择器")
    if not click_first_available(page, ["button[type='submit']", "input[type='submit']"]):
        raise RuntimeError("找不到 IMDb 提交按钮，需手动调整选择器")
    page.wait_for_timeout(1500)


def process_goodreads(p: Playwright, items: List[Dict], mapping: Dict[str, str]) -> None:
    browser = p.chromium.launch_persistent_context(
        user_data_dir=str(GOODREADS_PROFILE_DIR),
        headless=HEADLESS,
    )
    page = browser.new_page()
    prompt_login(page, "https://www.goodreads.com/", "Goodreads")
    for item in items:
        target_url = get_target_url(mapping, item)
        if not target_url:
            continue
        comment = (item.get("comment") or item.get("comment_en") or "").strip()
        if not comment:
            continue
        rating = convert_rating_for_goodreads(item.get("rating"))
        title_display = item.get("title") or item.get("title_en") or item.get("title_zh")
        print(f"[GOODREADS] {title_display} -> {target_url}")
        try:
            post_goodreads_review(page, target_url, rating, comment)
        except Exception as e:
            print(f"[WARN] fail {target_url}: {e}")
    browser.close()


def process_imdb(p: Playwright, items: List[Dict], mapping: Dict[str, str]) -> None:
    browser = p.chromium.launch_persistent_context(
        user_data_dir=str(IMDB_PROFILE_DIR),
        headless=HEADLESS,
    )
    page = browser.new_page()
    prompt_login(page, "https://www.imdb.com/", "IMDb")
    for item in items:
        target_url = get_target_url(mapping, item)
        if not target_url:
            continue
        comment = (item.get("comment") or item.get("comment_en") or "").strip()
        if not comment:
            continue
        rating = convert_rating_for_imdb(item.get("rating"))
        title_display = item.get("title") or item.get("title_en") or item.get("title_zh")
        print(f"[IMDb] {title_display} -> {target_url}")
        try:
            post_imdb_review(page, target_url, rating, comment)
        except Exception as e:
            print(f"[WARN] fail {target_url}: {e}")
    browser.close()


def main() -> None:
    books = load_list_json(BOOKS_FILE)
    movies = load_list_json(MOVIES_FILE)
    goodreads_map = load_mapping(GOODREADS_MAPPING_FILE)
    imdb_map = load_mapping(IMDB_MAPPING_FILE)

    if not goodreads_map and not imdb_map:
        print("[WARN] 映射文件为空，什么都不会发布。请填写 goodreads_targets.json / imdb_targets.json。")
        return

    with sync_playwright() as p:
        if goodreads_map:
            process_goodreads(p, books, goodreads_map)
        if imdb_map:
            process_imdb(p, movies, imdb_map)


if __name__ == "__main__":
    main()
