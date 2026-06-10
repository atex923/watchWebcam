#!/usr/bin/env python3
"""
WebCamTraveler

Fetches frames from public webcam/video URLs, saves them as JPG files, and
plays the latest captured images in a Tkinter slideshow.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import queue
import random
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional

import requests
from PIL import Image, ImageOps, ImageTk, UnidentifiedImageError

try:
    import cv2
except Exception:  # pragma: no cover - optional runtime dependency
    cv2 = None

try:
    import yt_dlp
except Exception:  # pragma: no cover - optional runtime dependency
    yt_dlp = None


APP_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = APP_DIR / "webcam_sources.json"
DEFAULT_CAPTURE_DIR = APP_DIR / "Temppic"
DEFAULT_PERMANENT_DIR = APP_DIR / "Livepic"
APP_VERSION = "V0.1.6"
APP_TITLE = "即時走看世界"
USER_AGENT = "Mozilla/5.0 (compatible; WorldWebcamViewer/1.0)"
CLEANUP_INTERVAL_MS = 60 * 60 * 1000


@dataclass(frozen=True)
class Camera:
    name: str
    url: str
    kind: str = "auto"
    country: str = ""
    interval_seconds: int = 20
    source_page: str = ""
    youtube_id: str = ""
    image_candidates: tuple[str, ...] = ()

    @property
    def slug(self) -> str:
        raw = f"{self.country}_{self.name}".strip("_")
        slug = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_").lower()
        identity = f"{self.url}|{self.source_page or self.name}"
        digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:8]
        return f"{slug or 'camera'}_{digest}"


@dataclass
class FrameEvent:
    camera: Camera
    path: Optional[Path]
    message: str
    ok: bool


class FrameCapturer:
    stream_cache: dict[str, tuple[float, str]] = {}
    stream_cache_lock = threading.Lock()
    stream_cache_seconds = 30 * 60

    def __init__(
        self,
        camera: Camera,
        capture_dir: Path,
        stop_event: threading.Event,
        keep_history: int,
        request_timeout: int,
    ) -> None:
        self.camera = camera
        self.capture_dir = capture_dir
        self.stop_event = stop_event
        self.keep_history = keep_history
        self.request_timeout = request_timeout

    def capture_once(self) -> Path:
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        image = self.fetch_image()
        path = self.save_image(image)
        self.prune_history()
        return path

    def fetch_image(self) -> Image.Image:
        kind = self.camera.kind.lower()
        if kind in {"youtube_thumbnail", "youtube-thumb", "youtube_thumb"}:
            return self.fetch_youtube_thumbnail()
        if kind in {"youtube", "youtube_stream", "youtube-live", "youtube_live"}:
            return self.fetch_youtube_stream()
        if kind in {"skyline_snapshot", "best_snapshot"}:
            return self.fetch_best_snapshot()
        if kind == "snapshot" or self.looks_like_snapshot(self.camera.url):
            return self.fetch_snapshot()
        if cv2 is not None:
            return self.fetch_with_opencv()
        return self.fetch_snapshot()

    def fetch_snapshot(self) -> Image.Image:
        response = requests.get(
            self.camera.url,
            headers={"User-Agent": USER_AGENT},
            timeout=self.request_timeout,
            stream=False,
        )
        response.raise_for_status()
        try:
            image = Image.open(BytesIO(response.content)).convert("RGB")
        except UnidentifiedImageError as exc:
            raise RuntimeError("無法把回應內容解析成圖片，請確認 URL 是 JPG/PNG 快照") from exc
        return image

    def fetch_best_snapshot(self) -> Image.Image:
        urls = self.camera.image_candidates or (self.camera.url,)
        best_image: Optional[Image.Image] = None
        best_area = -1
        errors = []
        for url in urls:
            try:
                image = self.fetch_snapshot_url(url)
            except Exception as exc:
                errors.append(f"{url}: {exc}")
                continue
            area = image.width * image.height
            if area > best_area:
                best_image = image
                best_area = area
        if best_image is None:
            raise RuntimeError("所有快照候選都無法轉成圖片：" + "；".join(errors[:3]))
        return best_image

    def fetch_snapshot_url(self, url: str) -> Image.Image:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=self.request_timeout,
            stream=False,
        )
        response.raise_for_status()
        try:
            return Image.open(BytesIO(response.content)).convert("RGB")
        except UnidentifiedImageError as exc:
            raise RuntimeError("無法把回應內容解析成圖片") from exc

    def fetch_youtube_thumbnail(self) -> Image.Image:
        youtube_id = self.camera.youtube_id or self.extract_youtube_id(self.camera.url)
        if not youtube_id:
            raise RuntimeError("找不到 YouTube ID，無法抓取縮圖")
        candidates = self.camera.image_candidates or self.youtube_thumbnail_urls(youtube_id)
        return self.fetch_best_snapshot_from_urls(candidates)

    def fetch_best_snapshot_from_urls(self, urls: tuple[str, ...]) -> Image.Image:
        best_image: Optional[Image.Image] = None
        best_area = -1
        errors = []
        for url in urls:
            try:
                image = self.fetch_snapshot_url(url)
            except Exception as exc:
                errors.append(f"{url}: {exc}")
                continue
            area = image.width * image.height
            if area > best_area:
                best_image = image
                best_area = area
        if best_image is None:
            raise RuntimeError("所有縮圖候選都無法轉成圖片：" + "；".join(errors[:3]))
        return best_image

    @staticmethod
    def youtube_thumbnail_urls(youtube_id: str) -> tuple[str, ...]:
        base = f"https://i.ytimg.com/vi/{youtube_id}"
        return (
            f"{base}/maxresdefault.jpg",
            f"{base}/sddefault.jpg",
            f"{base}/hqdefault.jpg",
            f"{base}/mqdefault.jpg",
            f"{base}/default.jpg",
        )

    @staticmethod
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

    def fetch_youtube_stream(self) -> Image.Image:
        if yt_dlp is None:
            raise RuntimeError("缺少 yt-dlp，請先執行：python3 -m pip install yt-dlp")
        youtube_url = self.camera.url
        if self.camera.youtube_id:
            youtube_url = f"https://www.youtube.com/watch?v={self.camera.youtube_id}"
        stream_url = self.resolve_youtube_stream_url(youtube_url)
        return self.fetch_with_opencv(stream_url)

    def resolve_youtube_stream_url(self, youtube_url: str) -> str:
        now = time.time()
        with self.stream_cache_lock:
            cached = self.stream_cache.get(youtube_url)
            if cached and cached[0] > now:
                return cached[1]

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": self.request_timeout,
            "format": "best",
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(youtube_url, download=False)
            except Exception as exc:
                raise RuntimeError(f"YouTube 串流解析失敗：{exc}") from exc

        stream_url = self.pick_stream_url(info)
        if not stream_url:
            raise RuntimeError("yt-dlp 找不到可讀取的 YouTube 串流 URL")

        with self.stream_cache_lock:
            self.stream_cache[youtube_url] = (now + self.stream_cache_seconds, stream_url)
        return stream_url

    @staticmethod
    def pick_stream_url(info: dict) -> str:
        direct_url = info.get("url")
        formats = info.get("formats") or []
        candidates = []
        for fmt in formats:
            url = fmt.get("url")
            if not url:
                continue
            protocol = fmt.get("protocol") or ""
            height = fmt.get("height") or 0
            ext = fmt.get("ext") or ""
            score = 0
            if protocol in {"m3u8", "m3u8_native"} or ".m3u8" in url:
                score += 100
            if ext in {"mp4", "webm"}:
                score += 20
            if height:
                score += height
            candidates.append((score, url))
        if candidates:
            candidates.sort(reverse=True, key=lambda item: item[0])
            return candidates[0][1]
        return direct_url or ""

    def fetch_with_opencv(self, url: Optional[str] = None) -> Image.Image:
        assert cv2 is not None
        cap = cv2.VideoCapture(url or self.camera.url)
        try:
            deadline = time.time() + self.request_timeout
            frame = None
            while time.time() < deadline and not self.stop_event.is_set():
                ok, frame = cap.read()
                if ok and frame is not None:
                    break
                time.sleep(0.2)
            if frame is None:
                raise RuntimeError("無法從串流讀取畫面，可能離線或 URL 不支援")
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            return Image.fromarray(rgb)
        finally:
            cap.release()

    def save_image(self, image: Image.Image) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{self.camera.slug}_{stamp}.jpg"
        path = self.capture_dir / filename
        latest = self.capture_dir / f"latest_{self.camera.slug}.jpg"
        image.save(path, "JPEG", quality=95, subsampling=0, optimize=True)
        shutil.copyfile(path, latest)
        return latest

    def prune_history(self) -> None:
        if self.keep_history <= 0:
            return
        files = sorted(
            self.capture_dir.glob(f"{self.camera.slug}_*.jpg"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for old_file in files[self.keep_history :]:
            old_file.unlink(missing_ok=True)

    @staticmethod
    def looks_like_snapshot(url: str) -> bool:
        clean = url.lower().split("?", 1)[0]
        return clean.endswith((".jpg", ".jpeg", ".png", ".webp"))


class CaptureQueueWorker(threading.Thread):
    def __init__(
        self,
        job_queue: "queue.Queue[Camera]",
        events: "queue.Queue[FrameEvent]",
        capture_dir: Path,
        stop_event: threading.Event,
        keep_history: int,
        request_timeout: int,
        schedule_lock: threading.Lock,
        queued: set[str],
        next_due: dict[str, float],
    ) -> None:
        super().__init__(daemon=True)
        self.job_queue = job_queue
        self.events = events
        self.capture_dir = capture_dir
        self.stop_event = stop_event
        self.keep_history = keep_history
        self.request_timeout = request_timeout
        self.schedule_lock = schedule_lock
        self.queued = queued
        self.next_due = next_due

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                camera = self.job_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                capturer = FrameCapturer(
                    camera,
                    self.capture_dir,
                    self.stop_event,
                    self.keep_history,
                    self.request_timeout,
                )
                path = capturer.capture_once()
                self.events.put(FrameEvent(camera, path, "已更新", True))
            except Exception as exc:
                self.events.put(FrameEvent(camera, None, str(exc), False))
            finally:
                with self.schedule_lock:
                    self.queued.discard(camera.slug)
                    self.next_due[camera.slug] = time.time() + max(5, camera.interval_seconds)
                self.job_queue.task_done()


class WebcamSlideshowApp:
    def __init__(
        self,
        cameras: list[Camera],
        capture_dir: Path,
        permanent_dir: Path,
        width: int,
        height: int,
        slide_seconds: int,
        keep_history: int,
        request_timeout: int,
        max_workers: int,
        initial_frames: int,
        buffer_batch_size: int,
    ) -> None:
        self.cameras = cameras
        self.capture_dir = capture_dir
        self.permanent_dir = permanent_dir
        self.width = width
        self.height = height
        self.slide_seconds = max(1, slide_seconds)
        self.slide_ms = self.slide_seconds * 1000
        self.events: "queue.Queue[FrameEvent]" = queue.Queue()
        self.job_queue: "queue.Queue[Camera]" = queue.Queue()
        self.stop_event = threading.Event()
        self.schedule_lock = threading.Lock()
        self.queued: set[str] = set()
        now = time.time()
        self.next_due: dict[str, float] = {
            camera.slug: now + max(5, camera.interval_seconds) for camera in cameras
        }
        self.initial_frames = max(1, initial_frames)
        self.buffer_batch_size = max(1, buffer_batch_size)
        self.background_index = min(self.initial_frames, len(cameras))
        self.round_number = 1
        self.round_started: set[str] = set()
        self.round_finished: set[str] = set()
        worker_count = max(1, min(max_workers, len(cameras)))
        self.workers = [
            CaptureQueueWorker(
                self.job_queue,
                self.events,
                capture_dir,
                self.stop_event,
                keep_history,
                request_timeout,
                self.schedule_lock,
                self.queued,
                self.next_due,
            )
            for _ in range(worker_count)
        ]
        self.latest: dict[str, Path] = {}
        self.status: dict[str, str] = {camera.slug: "等待擷取" for camera in cameras}
        self.index = 0
        self.current_country = ""
        self.current_camera: Optional[Camera] = None
        self.current_path: Optional[Path] = None
        self.paused = False
        self.panels_collapsed = False
        self.display_image: Optional[Image.Image] = None
        self.photo: Optional[ImageTk.PhotoImage] = None
        self.canvas_image_id: Optional[int] = None
        self.root = self.build_ui()

    def build_ui(self) -> "tkinter.Tk":
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.title(f"{APP_TITLE} {APP_VERSION}")
        root.geometry(f"{self.width + 360}x{self.height + 180}")
        root.minsize(420, 280)
        root.resizable(True, True)
        root.protocol("WM_DELETE_WINDOW", self.close)

        root.columnconfigure(0, weight=1)
        root.columnconfigure(1, weight=0)
        root.rowconfigure(0, weight=1)

        image_area = ttk.Frame(root)
        image_area.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        image_area.columnconfigure(0, weight=1)
        image_area.rowconfigure(1, weight=1)

        self.source_name_var = tk.StringVar(value="正在連線到直播攝影機...")
        ttk.Label(
            image_area,
            textvariable=self.source_name_var,
            font=("TkDefaultFont", 16, "bold"),
            anchor="center",
            wraplength=self.width,
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self.image_canvas = tk.Canvas(
            image_area,
            width=self.width,
            height=self.height,
            bg="#12151a",
            highlightthickness=0,
        )
        self.image_canvas.grid(row=1, column=0, sticky="nsew")
        self.image_canvas.bind("<Configure>", self.on_canvas_resize)
        self.canvas_image_id = self.image_canvas.create_image(
            self.width // 2,
            self.height // 2,
            anchor="center",
        )

        controls = ttk.Frame(image_area)
        controls.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        controls.columnconfigure(10, weight=1)

        self.pause_text = tk.StringVar(value="暫停")
        ttk.Button(controls, textvariable=self.pause_text, command=self.toggle_pause).grid(
            row=0, column=0, padx=(0, 8)
        )

        ttk.Label(controls, text="輪播間隔").grid(row=0, column=1, padx=(0, 6))
        self.interval_seconds_var = tk.IntVar(value=self.slide_seconds)
        self.interval_spinbox = tk.Spinbox(
            controls,
            from_=1,
            to=3600,
            width=6,
            textvariable=self.interval_seconds_var,
            command=self.apply_slide_interval,
        )
        self.interval_spinbox.grid(row=0, column=2, padx=(0, 6))
        self.interval_spinbox.bind("<Return>", self.apply_slide_interval)
        self.interval_spinbox.bind("<FocusOut>", self.apply_slide_interval)
        ttk.Label(controls, text="秒").grid(row=0, column=3, padx=(0, 12))
        ttk.Button(controls, text="套用", command=self.apply_slide_interval).grid(row=0, column=4, padx=(0, 8))
        ttk.Button(controls, text="開啟來源", command=self.open_current_source).grid(row=0, column=5, padx=(0, 8))
        ttk.Button(controls, text="永久儲存", command=self.save_current_permanently).grid(
            row=0, column=6, sticky="w"
        )

        self.always_on_top_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(controls, text="視窗頂置", variable=self.always_on_top_var).grid(
            row=0, column=7, padx=(12, 6)
        )
        ttk.Button(controls, text="執行", command=self.apply_always_on_top).grid(
            row=0, column=8, padx=(0, 8)
        )
        self.collapse_text = tk.StringVar(value="縮版")
        ttk.Button(controls, textvariable=self.collapse_text, width=8, command=self.toggle_panels).grid(
            row=0, column=9, padx=(0, 8)
        )

        self.source_url_var = tk.StringVar(value="來源網址：尚未顯示")
        self.source_url_label = ttk.Label(image_area, textvariable=self.source_url_var, wraplength=self.width)
        self.source_url_label.grid(
            row=3, column=0, sticky="ew", pady=(8, 0)
        )

        self.side = ttk.Frame(root, width=280)
        self.side.grid(row=0, column=1, sticky="ns", padx=(0, 12), pady=12)
        self.side.grid_propagate(False)
        self.side.rowconfigure(1, weight=1)

        self.title_var = tk.StringVar(value="抓取清單")
        ttk.Label(self.side, textvariable=self.title_var, font=("TkDefaultFont", 14, "bold")).grid(
            row=0, column=0, sticky="ew", pady=(0, 8)
        )

        self.status_text = tk.Text(self.side, height=18, width=34, wrap="word")
        self.status_text.grid(row=1, column=0, sticky="nsew")
        self.status_text.configure(state="disabled")

        self.side_buttons = ttk.Frame(self.side)
        self.side_buttons.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.side_buttons.columnconfigure(0, weight=1)
        self.side_buttons.columnconfigure(1, weight=1)

        ttk.Button(self.side_buttons, text="手動更新", command=self.refresh_all).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(self.side_buttons, text="開啟暫存", command=self.open_capture_dir).grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )

        self.footer_var = tk.StringVar(
            value=f"抓取狀況：等待 · {APP_VERSION} · 輪播間隔：{self.slide_seconds} 秒"
        )
        self.footer_label = ttk.Label(root, textvariable=self.footer_var)
        self.footer_label.grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 10)
        )
        return root

    def start(self) -> None:
        for worker in self.workers:
            worker.start()
        self.prime_initial_frames()
        self.root.after(250, self.process_events)
        self.root.after(1000, self.fill_background_buffer)
        self.root.after(600, self.advance_slide)
        self.root.after(CLEANUP_INTERVAL_MS, self.cleanup_captured_frames)
        self.root.mainloop()

    def prime_initial_frames(self) -> None:
        for camera in self.cameras[: self.initial_frames]:
            self.status[camera.slug] = f"第 {self.round_number} 輪啟動優先抓取"
            self.enqueue_camera(camera, force=True)
        for camera in self.cameras[self.initial_frames :]:
            self.status[camera.slug] = f"第 {self.round_number} 輪等待背景緩衝"
        self.render_status()

    def fill_background_buffer(self) -> None:
        target_queue_size = self.buffer_batch_size
        while (
            self.background_index < len(self.cameras)
            and self.job_queue.qsize() < target_queue_size
            and not self.stop_event.is_set()
        ):
            camera = self.cameras[self.background_index]
            self.background_index += 1
            self.status[camera.slug] = f"第 {self.round_number} 輪排入背景緩衝"
            self.enqueue_camera(camera, force=True)
        self.render_status()
        if self.round_is_complete():
            self.start_next_round()
        if not self.stop_event.is_set():
            self.root.after(1000, self.fill_background_buffer)

    def enqueue_camera(self, camera: Camera, force: bool = False) -> None:
        now = time.time()
        with self.schedule_lock:
            if not force and now < self.next_due.get(camera.slug, 0):
                return
            if camera.slug in self.queued:
                return
            self.queued.add(camera.slug)
            self.round_started.add(camera.slug)
            if force:
                self.next_due[camera.slug] = 0
            self.job_queue.put(camera)

    def schedule_refreshes(self) -> None:
        # Round-robin refresh is handled by fill_background_buffer().
        return

    def round_is_complete(self) -> bool:
        return (
            self.background_index >= len(self.cameras)
            and not self.queued
            and self.job_queue.empty()
            and len(self.round_finished) >= len(self.cameras)
        )

    def start_next_round(self) -> None:
        self.round_number += 1
        self.background_index = 0
        self.round_started.clear()
        self.round_finished.clear()
        for camera in self.cameras:
            self.status[camera.slug] = f"第 {self.round_number} 輪等待背景緩衝"
        self.update_footer(f"抓取狀況：第 {self.round_number - 1} 輪完成，開始第 {self.round_number} 輪")

    def process_events(self) -> None:
        changed = False
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            changed = True
            slug = event.camera.slug
            if event.ok and event.path:
                self.latest[slug] = event.path
            self.status[slug] = event.message
            self.round_finished.add(slug)
            if self.current_camera and self.current_camera.slug == slug:
                self.update_status_bar()
        if changed:
            self.render_status()
        if not self.stop_event.is_set():
            self.root.after(250, self.process_events)

    def render_status(self) -> None:
        lines = []
        for camera in self.cameras:
            marker = "OK" if camera.slug in self.latest else "--"
            lines.append(f"[{marker}] {camera.country} {camera.name}\n{self.status[camera.slug]}\n")
        self.status_text.configure(state="normal")
        self.status_text.delete("1.0", "end")
        self.status_text.insert("1.0", "\n".join(lines).strip())
        self.status_text.configure(state="disabled")

    def advance_slide(self) -> None:
        if not self.paused:
            available = [
                (camera, self.latest[camera.slug])
                for camera in self.cameras
                if camera.slug in self.latest and self.latest[camera.slug].exists()
            ]
            if available:
                camera, path = self.pick_next_slide(available)
                self.current_country = camera.country
                self.current_camera = camera
                self.show_image(camera, path)
        if not self.stop_event.is_set():
            self.root.after(self.slide_ms, self.advance_slide)

    def pick_next_slide(self, available: list[tuple[Camera, Path]]) -> tuple[Camera, Path]:
        self.index %= len(available)
        for offset in range(len(available)):
            candidate_index = (self.index + offset) % len(available)
            camera, path = available[candidate_index]
            if len({item[0].country for item in available}) == 1 or camera.country != self.current_country:
                self.index = (candidate_index + 1) % len(available)
                return camera, path
        camera, path = available[self.index]
        self.index = (self.index + 1) % len(available)
        return camera, path

    def show_image(self, camera: Camera, path: Path) -> None:
        self.current_path = path
        self.display_image = Image.open(path).convert("RGB")
        self.render_display_image()
        self.source_name_var.set(f"{camera.country} · {camera.name}")
        self.update_status_bar()
        self.source_url_var.set(f"來源網址：{self.current_source_url(camera)}")

    def on_canvas_resize(self, _event: object) -> None:
        self.render_display_image()

    def render_display_image(self) -> None:
        if self.display_image is None or self.canvas_image_id is None:
            return
        canvas_width = max(1, self.image_canvas.winfo_width())
        canvas_height = max(1, self.image_canvas.winfo_height())
        image = ImageOps.contain(
            self.display_image,
            (canvas_width, canvas_height),
            Image.Resampling.LANCZOS,
        )
        canvas = Image.new("RGB", (canvas_width, canvas_height), (18, 21, 26))
        x = (canvas_width - image.width) // 2
        y = (canvas_height - image.height) // 2
        canvas.paste(image, (x, y))

        self.photo = ImageTk.PhotoImage(canvas)
        self.image_canvas.coords(self.canvas_image_id, canvas_width // 2, canvas_height // 2)
        self.image_canvas.itemconfigure(self.canvas_image_id, image=self.photo)

    def update_status_bar(self) -> None:
        if not self.current_camera:
            return
        self.update_footer(self.current_status_text(self.current_camera))

    def current_status_text(self, camera: Camera) -> str:
        buffered = len(self.latest)
        queued = self.job_queue.qsize()
        message = self.status.get(camera.slug, "等待")
        return (
            f"抓取狀況：第 {self.round_number} 輪 {len(self.round_finished)}/{len(self.cameras)} "
            f"· {message} · 緩衝 {buffered}/{len(self.cameras)} · 佇列 {queued}"
        )

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self.pause_text.set("繼續" if self.paused else "暫停")

    def apply_always_on_top(self) -> None:
        enabled = bool(self.always_on_top_var.get())
        self.root.attributes("-topmost", enabled)
        self.update_footer("視窗已頂置" if enabled else "視窗頂置已取消")

    def toggle_panels(self) -> None:
        self.panels_collapsed = not self.panels_collapsed
        if self.panels_collapsed:
            self.source_url_label.grid_remove()
            self.side.grid_remove()
            self.footer_label.grid_remove()
            self.collapse_text.set("控制欄")
        else:
            self.source_url_label.grid()
            self.side.grid()
            self.footer_label.grid()
            self.collapse_text.set("縮版")

    def apply_slide_interval(self, *_: object) -> None:
        try:
            seconds = int(self.interval_seconds_var.get())
        except Exception:
            seconds = self.slide_seconds
        seconds = max(1, min(3600, seconds))
        self.slide_seconds = seconds
        self.slide_ms = seconds * 1000
        self.interval_seconds_var.set(seconds)
        self.update_footer(f"輪播間隔已更新為 {seconds} 秒")

    def current_source_url(self, camera: Camera) -> str:
        return camera.source_page or camera.url

    def open_url(self, url: str) -> None:
        if not url:
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", url])
        elif os.name == "nt":
            os.startfile(url)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", url])

    def open_current_source(self) -> None:
        if not self.current_camera:
            self.update_footer("目前尚未顯示來源")
            return
        self.open_url(self.current_source_url(self.current_camera))

    def save_current_permanently(self) -> None:
        if not self.current_camera or not self.current_path or not self.current_path.exists():
            self.update_footer("目前沒有可永久儲存的圖片")
            return
        self.permanent_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        destination = self.permanent_dir / f"{self.current_camera.slug}_{stamp}.jpg"
        shutil.copyfile(self.current_path, destination)
        self.update_footer(f"已永久儲存：{destination.name}")

    def refresh_all(self) -> None:
        self.round_number += 1
        self.background_index = min(self.initial_frames, len(self.cameras))
        self.round_started.clear()
        self.round_finished.clear()
        for camera in self.cameras[: self.initial_frames]:
            self.status[camera.slug] = f"第 {self.round_number} 輪手動優先更新"
            self.enqueue_camera(camera, force=True)
        for camera in self.cameras[self.initial_frames :]:
            self.status[camera.slug] = f"第 {self.round_number} 輪等待背景緩衝"
        self.fill_background_buffer()

    def open_capture_dir(self) -> None:
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(self.capture_dir)])
        elif os.name == "nt":
            os.startfile(self.capture_dir)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(self.capture_dir)])

    def update_footer(self, message: str = "") -> None:
        parts = [
            message or "抓取狀況：等待",
            APP_VERSION,
            f"輪播間隔：{self.slide_seconds} 秒",
        ]
        self.footer_var.set(" · ".join(parts))

    def cleanup_captured_frames(self) -> None:
        deleted = 0
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        for image_path in self.capture_dir.glob("*.jpg"):
            if image_path.name.startswith("latest_"):
                continue
            try:
                image_path.unlink()
                deleted += 1
            except OSError:
                continue
        self.footer_var.set(
            f"抓取狀況：每小時清理完成，刪除 {deleted} 張歷史圖 · {APP_VERSION} "
            f"· 輪播間隔：{self.slide_seconds} 秒"
        )
        if not self.stop_event.is_set():
            self.root.after(CLEANUP_INTERVAL_MS, self.cleanup_captured_frames)

    def close(self) -> None:
        self.stop_event.set()
        self.root.after(200, self.root.destroy)


def load_config_data(config_path: Path, seen: Optional[set[Path]] = None) -> dict:
    if not config_path.exists():
        raise FileNotFoundError(f"找不到設定檔：{config_path}")
    resolved = config_path.resolve()
    seen = seen or set()
    if resolved in seen:
        return {"cameras": []}
    seen.add(resolved)
    with config_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    cameras = list(data.get("cameras", []))
    for include_file in data.get("include_files", []):
        include_path = Path(include_file)
        if not include_path.is_absolute():
            include_path = config_path.parent / include_path
        included = load_config_data(include_path, seen)
        cameras.extend(included.get("cameras", []))
    data["cameras"] = cameras
    return data


def load_cameras(config_path: Path) -> list[Camera]:
    data = load_config_data(config_path)
    cameras = []
    identities = set()
    for item in data.get("cameras", []):
        if item.get("enabled", True) is False:
            continue
        identity = (
            item.get("kind", "auto"),
            item.get("url", ""),
            item.get("source_page", ""),
            item.get("name", ""),
        )
        if identity in identities:
            continue
        identities.add(identity)
        cameras.append(
            Camera(
                name=item["name"],
                url=item["url"],
                kind=item.get("kind", "auto"),
                country=item.get("country", ""),
                interval_seconds=int(item.get("interval_seconds", data.get("interval_seconds", 20))),
                source_page=item.get("source_page", ""),
                youtube_id=item.get("youtube_id", ""),
                image_candidates=tuple(item.get("image_candidates", ())),
            )
        )
    if not cameras:
        raise ValueError("設定檔裡沒有啟用中的攝影機")
    return interleave_cameras_by_country(cameras)


def interleave_cameras_by_country(cameras: list[Camera]) -> list[Camera]:
    groups: dict[str, list[Camera]] = collections.defaultdict(list)
    for camera in cameras:
        groups[camera.country or "未知"].append(camera)
    for group in groups.values():
        random.shuffle(group)

    ordered: list[Camera] = []
    last_country = ""
    while groups:
        choices = [
            (country, items)
            for country, items in groups.items()
            if country != last_country or len(groups) == 1
        ]
        choices.sort(key=lambda item: len(item[1]), reverse=True)
        country, items = random.choice(choices[: min(4, len(choices))])
        ordered.append(items.pop())
        last_country = country
        if not items:
            del groups[country]
    return ordered


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抓取公開直播攝影機畫面並輪播顯示")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="攝影機來源 JSON 設定檔")
    parser.add_argument("--capture-dir", type=Path, default=DEFAULT_CAPTURE_DIR, help="暫存 JPG 圖檔輸出資料夾")
    parser.add_argument("--permanent-dir", type=Path, default=DEFAULT_PERMANENT_DIR, help="永久儲存 JPG 圖檔資料夾")
    parser.add_argument("--width", type=int, default=960, help="輪播畫面寬度")
    parser.add_argument("--height", type=int, default=540, help="輪播畫面高度")
    parser.add_argument("--slide-seconds", type=int, default=5, help="每張圖輪播秒數")
    parser.add_argument("--keep-history", type=int, default=30, help="每台攝影機保留幾張歷史圖")
    parser.add_argument("--request-timeout", type=int, default=12, help="每次讀取逾時秒數")
    parser.add_argument("--max-workers", type=int, default=8, help="同時抓圖的背景工作數")
    parser.add_argument("--initial-frames", type=int, default=5, help="啟動時優先抓取幾個畫面")
    parser.add_argument("--buffer-batch-size", type=int, default=24, help="背景緩衝佇列最多先排幾個來源")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cameras = load_cameras(args.config)
    app = WebcamSlideshowApp(
        cameras=cameras,
        capture_dir=args.capture_dir,
        permanent_dir=args.permanent_dir,
        width=args.width,
        height=args.height,
        slide_seconds=args.slide_seconds,
        keep_history=args.keep_history,
        request_timeout=args.request_timeout,
        max_workers=args.max_workers,
        initial_frames=args.initial_frames,
        buffer_batch_size=args.buffer_batch_size,
    )

    signal.signal(signal.SIGINT, lambda *_: app.close())
    app.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
