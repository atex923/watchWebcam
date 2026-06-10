#!/usr/bin/env python3
"""
Refresh GuideMe24 webcam data from an existing source-page list.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup


YOUTUBE_RE = re.compile(
    r"(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=|live/)|youtu\.be/)([A-Za-z0-9_-]{6,})"
)


def normalize_title(text: str) -> str:
    text = text.replace(" | GuideMe24全球各地即時影像", "")
    text = text.replace("GuideMe24-全世界各地LIVE時況,即時影像攝影機", "")
    return re.sub(r"\s+", " ", text).strip()


def infer_country(name: str) -> str:
    keyword_map = [
        ("台灣", ["台灣", "臺灣", "台北", "新北", "桃園", "台中", "台南", "高雄", "基隆", "宜蘭", "新竹", "苗栗", "彰化", "南投", "雲林", "嘉義", "屏東", "台東", "臺東", "花蓮", "澎湖", "金門", "馬祖", "綠島"]),
        ("日本", ["日本", "東京", "大阪", "京都", "北海道", "神奈川", "靜岡", "長野", "山梨", "沖繩", "千葉", "廣島", "長崎", "山形", "岐阜", "兵庫", "栃木", "熊本", "富山", "群馬", "島根", "石川", "福島", "秋田", "鳥取", "和歌山", "大分", "新潟", "岩手", "愛媛", "德島", "宮崎", "宮城", "奈良", "滋賀", "福井", "茨城", "佐賀", "鹿兒島", "宮島", "三重", "青森", "埼玉"]),
        ("美國", ["美國", "夏威夷", "芝加哥", "紐約", "加州", "阿拉斯加", "洛杉磯", "拉斯維加斯"]),
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
        ("印尼", ["印尼", "峇里島", "巴里島", "Bali", "ubud", "烏布"]),
    ]
    for country, keywords in keyword_map:
        if any(keyword in name for keyword in keywords):
            return country
    prefix = re.split(r"[-,，(（ ]", name, maxsplit=1)[0].strip()
    return prefix or "未知"


def load_seed_pages(seed_path: Path) -> list[dict]:
    data = json.loads(seed_path.read_text(encoding="utf-8"))
    seen = set()
    pages = []
    for item in data.get("cameras", []):
        source_page = item.get("source_page", "").split("#", 1)[0]
        if not source_page or source_page in seen:
            continue
        seen.add(source_page)
        pages.append(item)
    return pages


def extract_page(session: requests.Session, seed_item: dict, timeout: int, interval_seconds: int) -> tuple[list[dict], str]:
    url = seed_item["source_page"]
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    title = soup.find("h1") or soup.find("title")
    name = normalize_title(title.get_text(" ", strip=True) if title else seed_item.get("name", ""))
    if not name:
        name = seed_item.get("name") or url.rstrip("/").rsplit("/", 1)[-1]
    country = seed_item.get("country") or infer_country(name)

    video_ids = []
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src") or ""
        match = YOUTUBE_RE.search(src)
        if match:
            video_ids.append(match.group(1))
    if not video_ids:
        video_ids = [match.group(1) for match in YOUTUBE_RE.finditer(response.text)]

    cameras = []
    for video_id in dict.fromkeys(video_ids):
        cameras.append(
            {
                "name": name,
                "country": country,
                "kind": "youtube_stream",
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "interval_seconds": interval_seconds,
                "source_page": url,
                "youtube_id": video_id,
            }
        )
    return cameras, name


def refresh(args: argparse.Namespace) -> dict:
    seed_items = load_seed_pages(args.seed)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
        }
    )

    cameras = []
    failed = []
    empty = []
    limit_items = seed_items[: args.limit] if args.limit else seed_items
    for index, item in enumerate(limit_items, start=1):
        try:
            found, name = extract_page(session, item, args.timeout, args.interval_seconds)
        except Exception as exc:
            failed.append({"source_page": item.get("source_page", ""), "error": str(exc)})
            print(f"[{index}/{len(limit_items)}] fail {item.get('source_page')}: {exc}")
            time.sleep(args.delay)
            continue
        if not found:
            empty.append({"source_page": item.get("source_page", ""), "name": name})
            print(f"[{index}/{len(limit_items)}] empty {name}")
        else:
            cameras.extend(found)
            if index % args.progress_every == 0 or index == len(limit_items):
                print(f"[{index}/{len(limit_items)}] cameras={len(cameras)}")
        time.sleep(args.delay)

    report = {
        "seed_pages": len(limit_items),
        "cameras": len(cameras),
        "failed": failed,
        "empty": empty,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "interval_seconds": args.interval_seconds,
        "slide_seconds": args.slide_seconds,
        "refreshed_from": str(args.seed),
        "report": str(args.report),
        "cameras": cameras,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用舊 GuideMe24 來源清單逐頁重新抓取 YouTube 來源")
    parser.add_argument("--seed", type=Path, default=Path("outputs/guideme24_webcams.json"))
    parser.add_argument("--output", type=Path, default=Path("outputs/guideme24_webcams_refreshed.json"))
    parser.add_argument("--report", type=Path, default=Path("outputs/guideme24_refresh_report.json"))
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--delay", type=float, default=0.05)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--interval-seconds", type=int, default=120)
    parser.add_argument("--slide-seconds", type=int, default=5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = refresh(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {len(config['cameras'])} cameras to {args.output}")
    print(f"report saved to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
