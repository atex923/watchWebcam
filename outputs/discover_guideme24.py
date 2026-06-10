#!/usr/bin/env python3
"""
Discover GuideMe24 product pages and build a webcam config for the viewer.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://guideme24.com/"
YOUTUBE_RE = re.compile(
    r"(?:youtube\.com/embed/|youtu\.be/|youtube\.com/watch\?v=)([A-Za-z0-9_-]{6,})"
)


def get_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def normalize_title(text: str) -> str:
    text = text.replace(" | GuideMe24全球各地即時影像", "")
    return re.sub(r"\s+", " ", text).strip()


def infer_country(name: str) -> str:
    keyword_map = [
        ("台灣", ["台北", "新北", "桃園", "台中", "台南", "高雄", "基隆", "宜蘭", "新竹", "苗栗", "彰化", "南投", "雲林", "嘉義", "屏東", "台東", "花蓮", "澎湖", "金門", "馬祖", "綠島", "臺東", "桃園國際機場"]),
        ("日本", ["日本", "東京", "大阪", "京都", "北海道", "神奈川", "靜岡", "長野", "山梨", "沖繩", "千葉", "廣島", "長崎", "山形", "岐阜", "兵庫", "栃木", "熊本", "富山", "群馬", "島根", "石川", "福島", "秋田", "鳥取", "和歌山", "大分", "新潟", "岩手", "愛媛", "德島", "宮崎", "宮城", "奈良", "滋賀", "福井", "茨城", "佐賀", "鹿兒島", "宮島", "三重縣", "青森", "埼玉"]),
        ("美國", ["美國", "夏威夷", "芝加哥", "紐約", "加州", "阿拉斯加"]),
        ("加拿大", ["加拿大"]),
        ("南韓", ["南韓", "韓國", "首爾"]),
        ("泰國", ["泰國", "曼谷", "蘇梅島"]),
        ("義大利", ["義大利"]),
        ("法國", ["法國"]),
        ("西班牙", ["西班牙"]),
        ("荷蘭", ["荷蘭"]),
        ("波蘭", ["波蘭"]),
        ("希臘", ["希臘"]),
        ("挪威", ["挪威"]),
        ("德國", ["德國"]),
        ("芬蘭", ["芬蘭"]),
        ("瑞士", ["瑞士"]),
        ("墨西哥", ["墨西哥"]),
        ("庫拉索", ["庫拉索"]),
        ("美屬維爾京群島", ["美屬維爾京群島"]),
        ("格陵蘭", ["格陵蘭"]),
        ("納米比亞", ["納米比亞"]),
        ("肯亞", ["肯亞"]),
        ("洪都拉斯", ["洪都拉斯"]),
        ("牙買加", ["牙買加"]),
        ("印尼", ["峇里島", "巴里島", "Bali", "ubud", "烏布"]),
    ]
    for country, keywords in keyword_map:
        if any(keyword in name for keyword in keywords):
            return country
    prefix = re.split(r"[-,，(（ ]", name, maxsplit=1)[0].strip()
    return prefix or "未知"


def collect_links(soup: BeautifulSoup) -> tuple[set[str], set[str]]:
    category_urls: set[str] = set()
    product_urls: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        url = urljoin(BASE_URL, anchor["href"].strip()).split("#", 1)[0]
        if "guideme24.com" not in url:
            continue
        if "/categorys" in url:
            category_urls.add(url)
        if "/products/" in url:
            product_urls.add(url)
    return category_urls, product_urls


def discover(args: argparse.Namespace) -> dict:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; WorldWebcamViewer/1.0)"})

    home = get_soup(session, BASE_URL, args.timeout)
    category_urls, product_urls = collect_links(home)
    category_urls.add(BASE_URL)

    for index, url in enumerate(sorted(category_urls), 1):
        try:
            soup = get_soup(session, url, args.timeout)
        except requests.RequestException as exc:
            print(f"[skip category] {url}: {exc}")
            continue
        _, products = collect_links(soup)
        product_urls.update(products)
        print(f"[category {index}/{len(category_urls)}] products={len(product_urls)}")
        time.sleep(args.delay)

    cameras = []
    seen_pages: set[str] = set()
    for index, url in enumerate(sorted(product_urls), 1):
        if url in seen_pages:
            continue
        seen_pages.add(url)
        try:
            soup = get_soup(session, url, args.timeout)
        except requests.RequestException as exc:
            print(f"[skip product] {url}: {exc}")
            continue

        title = soup.find("h1") or soup.find("title")
        name = normalize_title(title.get_text(" ", strip=True) if title else url.rsplit("/", 1)[-1])
        html = str(soup)
        video_ids = []
        for iframe in soup.find_all("iframe"):
            match = YOUTUBE_RE.search(iframe.get("src") or "")
            if match:
                video_ids.append(match.group(1))
        if not video_ids:
            video_ids = [match.group(1) for match in YOUTUBE_RE.finditer(html)]

        for video_id in dict.fromkeys(video_ids):
            cameras.append(
                {
                    "name": name,
                    "country": infer_country(name),
                    "kind": "youtube_stream",
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "interval_seconds": args.interval_seconds,
                    "source_page": url,
                    "youtube_id": video_id,
                }
            )
        if index % 25 == 0:
            print(f"[product {index}/{len(product_urls)}] cameras={len(cameras)}")
        time.sleep(args.delay)

    return {
        "interval_seconds": args.interval_seconds,
        "slide_seconds": args.slide_seconds,
        "cameras": cameras,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="掃描 GuideMe24 並產生 webcam 設定檔")
    parser.add_argument("--output", type=Path, default=Path("outputs/guideme24_webcams.json"))
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--delay", type=float, default=0.05)
    parser.add_argument("--interval-seconds", type=int, default=120)
    parser.add_argument("--slide-seconds", type=int, default=5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = discover(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {len(config['cameras'])} cameras to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
