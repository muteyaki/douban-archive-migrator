"""翻译书/电影数据，产出英文标题、作者/导演及评论。"""

import json
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

from openai import OpenAI
from tqdm import tqdm


BOOK_RAW_FILE = "douban_books_raw.json"
MOVIE_RAW_FILE = "douban_movies_raw.json"
BOOK_TRANSLATED_FILE = "douban_books_translated.json"
MOVIE_TRANSLATED_FILE = "douban_movies_translated.json"
ALL_TRANSLATED_FILE = "douban_export_translated.json"

QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-plus")

qwen_client = OpenAI(api_key=QWEN_API_KEY, base_url=QWEN_BASE_URL)

REVIEW_SYSTEM_PROMPT = (
    "You are a professional translator specializing in reviews of books and films. "
    "Translate the following Chinese review into fluent, natural English, "
    "as if it were written by a native user on an English review site. "
    "Preserve meaning, tone, and emotional nuance, but adapt culture-specific references "
    "to be understandable for international readers. "
    "Output only the translated English review, without any explanations."
)

STRUCTURED_SYSTEM_PROMPT = (
    "You are a bilingual expert who maps Douban entries to official English data. "
    "Return a JSON object with three fields: "
    "title_en (official English release title; if multiple aliases exist, pick the widely used official one), "
    "comment_en (natural English translation of the review; empty string if absent), and "
    "people_en (English names for the provided people list; do not include nationalities or extra descriptors; use empty string when unknown). "
    "You will receive Chinese title plus author/director context to disambiguate. "
    "Only return JSON, no extra text."
)

PERSON_SYSTEM_PROMPT = (
    "You transliterate and normalize Chinese personal names into their standard English renderings. "
    "Return only the English name, no explanations, no brackets, no nationality, no titles."
)


def translate_text(text: str) -> str:
    if not text.strip():
        return ""
    resp = qwen_client.chat.completions.create(
        model=QWEN_MODEL,
        messages=[
            {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0.2,
        max_tokens=800,
    )
    return resp.choices[0].message.content.strip()


def translate_people_list(people: List[str]) -> List[str]:
    translated: List[str] = []
    for p in people:
        name = p.strip()
        if not name:
            continue
        try:
            resp = qwen_client.chat.completions.create(
                model=QWEN_MODEL,
                messages=[
                    {"role": "system", "content": PERSON_SYSTEM_PROMPT},
                    {"role": "user", "content": name},
                ],
                temperature=0.0,
                max_tokens=50,
            )
            en_name = resp.choices[0].message.content.strip()
        except Exception:
            en_name = name
        translated.append(en_name)
    return translated


def detect_english_from_title(raw_title: str) -> Optional[str]:
    """从原始标题里提取已有英文别名（/、| 分隔）。"""
    if not raw_title:
        return None
    parts = [p.strip() for p in re.split(r"[／/|]", raw_title) if p.strip()]
    for p in parts:
        if re.search(r"[A-Za-z]", p):
            return p
    return None


def translate_title_and_comment(
    title_zh: str,
    comment_zh: str,
    category: str,
    people: List[str],
) -> Dict[str, str]:
    payload = {
        "category": category or "work",
        "title_zh": title_zh,
        "comment_zh": comment_zh,
        "people": people,
    }
    messages = [
        {"role": "system", "content": STRUCTURED_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]

    title_en = ""
    comment_en = ""
    people_en: List[str] = []

    try:
        resp = qwen_client.chat.completions.create(
            model=QWEN_MODEL,
            messages=messages,
            temperature=0.2,
            max_tokens=800,
        )
        content = resp.choices[0].message.content.strip()
        data = json.loads(content)
        title_en = (data.get("title_en") or "").strip()
        comment_en = (data.get("comment_en") or "").strip()
        raw_people_en = data.get("people_en")
        if isinstance(raw_people_en, list):
            people_en = [(p or "").strip() for p in raw_people_en if isinstance(p, str)]
    except Exception as e:
        print(f"\n[WARN] structured translation failed, falling back: {e}")
        title_en = translate_text(title_zh)
        comment_en = translate_text(comment_zh)
        people_en = []
    return {"title_en": title_en, "comment_en": comment_en, "people_en": people_en}


def translate_all(items: List[Dict], save_path: Path) -> List[Dict]:
    translated: List[Dict] = []
    for item in tqdm(items, desc=f"Translating -> {save_path.name}"):
        title_zh = (item.get("title_zh") or "").strip()
        comment_zh = (item.get("comment_zh") or "").strip()
        category = (item.get("category") or "").strip()

        people: List[str] = []
        if category == "book":
            people = item.get("authors_zh") or []
        elif category == "movie":
            people = item.get("directors_zh") or []

        source_url = item.get("subject_url") or ""

        detected_en = detect_english_from_title(title_zh) if category == "movie" else None

        try:
            trans = translate_title_and_comment(title_zh, comment_zh, category, people)
        except Exception as e:
            print(f"\n[ERROR] translate failed: {e}")
            trans = {"title_en": "", "comment_en": ""}

        title_en_final = detected_en or trans.get("title_en", "")
        comment_en_final = trans.get("comment_en", "")
        people_en = trans.get("people_en") if isinstance(trans, dict) else []
        if not isinstance(people_en, list):
            people_en = []
        rating_en = item.get("rating") if isinstance(item.get("rating"), int) else None

        out = {
            "category": category,
            "title": title_en_final,
            "comment": comment_en_final,
            "rating": rating_en,
            "subject_url": source_url,
        }
        if category == "book":
            names = people_en if people_en else translate_people_list(people)
            out["author"] = " / ".join(names)
        elif category == "movie":
            names = people_en if people_en else translate_people_list(people)
            out["director"] = " / ".join(names)

        translated.append(out)

        save_path.write_text(
            json.dumps(translated, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        time.sleep(0.5)

    return translated


def load_items(path: Path) -> List[Dict]:
    if not path.exists():
        print(f"[WARN] {path} not found, skip.")
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} 内容不是列表")
    return data


def translate_category(raw_file: str, translated_file: str) -> List[Dict]:
    items = load_items(Path(raw_file))
    if not items:
        return []
    return translate_all(items, Path(translated_file))


def main():
    if not QWEN_API_KEY:
        raise ValueError("请设置 QWEN_API_KEY（DashScope 密钥）。")

    books = translate_category(BOOK_RAW_FILE, BOOK_TRANSLATED_FILE)
    movies = translate_category(MOVIE_RAW_FILE, MOVIE_TRANSLATED_FILE)

    all_items = books + movies
    Path(ALL_TRANSLATED_FILE).write_text(
        json.dumps(all_items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"[SUMMARY] translated books={len(books)}, movies={len(movies)}, total={len(all_items)}"
    )


if __name__ == "__main__":
    main()
