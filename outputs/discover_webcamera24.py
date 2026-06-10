#!/usr/bin/env python3
"""
Discover Webcamera24 camera pages and build a YouTube stream config.
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


BASE_URL = "https://webcamera24.com"
START_URLS = (
    "https://webcamera24.com/zh/",
    "https://webcamera24.com/zh/popular/",
    "https://webcamera24.com/zh/latest/",
)
USER_AGENT = "Mozilla/5.0 (compatible; WebCamTraveler/1.0)"


def fetch(session: requests.Session, url: str, timeout: int) -> str:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def camera_links(html: str, page_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    seen = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if "/zh/camera/" not in href:
            continue
        url = urljoin(page_url, href)
        if url not in seen:
            seen.add(url)
            links.append(url)
    return links


def extract_youtube_id(url: str) -> str:
    patterns = (
        r"(?:v=|/embed/|youtu\.be/|/shorts/)([A-Za-z0-9_-]{6,})",
        r"youtube(?:-nocookie)?\.com/live/([A-Za-z0-9_-]{6,})",
    )
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return ""


def parse_detail(html: str, page_url: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    heading = soup.find("h1")
    if heading:
        title = " ".join(heading.get_text(" ", strip=True).split())
    if not title and soup.title:
        title = " ".join(soup.title.get_text(" ", strip=True).split())
    title = re.sub(r"\s*在线网络摄像头\s*$", "", title).strip()
    title = re.sub(r"^网络摄像头在线\s*", "", title).strip()

    country = ""
    for row in soup.find_all("tr"):
        text = " ".join(row.get_text(" ", strip=True).split())
        if text.startswith("国家:"):
            country = text.replace("国家:", "", 1).strip()
            break
    if not country:
        breadcrumbs = [item.get_text(" ", strip=True) for item in soup.select(".breadcrumbs span")]
        if len(breadcrumbs) >= 3:
            country = breadcrumbs[2].strip()

    raw = html
    urls = []
    urls.extend(re.findall(r'"contentUrl"\s*:\s*"(https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]+)"', raw))
    urls.extend(re.findall(r'"embedUrl"\s*:\s*"(https://www\.youtube(?:-nocookie)?\.com/embed/[A-Za-z0-9_-]+)"', raw))
    urls.extend(re.findall(r"https://www\.youtube(?:-nocookie)?\.com/embed/[A-Za-z0-9_-]+", raw))
    urls.extend(re.findall(r"https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]+", raw))
    youtube_id = ""
    for url in urls:
        youtube_id = extract_youtube_id(url)
        if youtube_id:
            break
    if not youtube_id:
        return None

    return {
        "name": title or page_url.rstrip("/").rsplit("/", 1)[-1],
        "country": country,
        "kind": "youtube_stream",
        "url": f"https://www.youtube.com/watch?v={youtube_id}",
        "interval_seconds": 120,
        "source_page": page_url,
        "youtube_id": youtube_id,
    }


def discover(args: argparse.Namespace) -> dict:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7"})

    detail_urls = []
    seen_urls = set()
    for start_url in START_URLS:
        try:
            html = fetch(session, start_url, args.timeout)
        except Exception as exc:
            print(f"[skip] {start_url}: {exc}")
            continue
        for url in camera_links(html, start_url):
            if url not in seen_urls:
                seen_urls.add(url)
                detail_urls.append(url)
            if len(detail_urls) >= args.max_pages:
                break
        if len(detail_urls) >= args.max_pages:
            break

    cameras = []
    seen_ids = set()
    for index, url in enumerate(detail_urls, start=1):
        try:
            html = fetch(session, url, args.timeout)
            item = parse_detail(html, url)
        except Exception as exc:
            print(f"[detail {index}/{len(detail_urls)}] skip {url}: {exc}")
            item = None
        if item and item["youtube_id"] not in seen_ids:
            seen_ids.add(item["youtube_id"])
            cameras.append(item)
            print(f"[detail {index}/{len(detail_urls)}] {item['country']} {item['name']}")
        time.sleep(args.delay)

    return {
        "interval_seconds": 120,
        "slide_seconds": 5,
        "source": "https://webcamera24.com/zh/",
        "capture_mode": "youtube_stream",
        "cameras": cameras,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="產生 Webcamera24 的 YouTube 直播來源清單")
    parser.add_argument("--output", type=Path, default=Path("outputs/webcamera24_webcams.json"))
    parser.add_argument("--max-pages", type=int, default=60)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--delay", type=float, default=0.2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = discover(args)
    args.output.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {len(config['cameras'])} cameras to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
