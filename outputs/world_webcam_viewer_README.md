# 即時走看世界 WebCamTraveler V0.1.12

這是一個 Python 桌面程式，可以抓取公開直播攝影機、YouTube 直播串流或即時快照 URL，把畫面轉成 JPG 圖檔，並在視窗中輪播最新畫面。

## 安裝套件

```bash
python3 -m pip install -r outputs/world_webcam_viewer_requirements.txt
```

## 執行

```bash
python3 outputs/WebCamTraveler_V0.1.12.py
```

也同時提供 `.pyw` 版本，適合不想顯示終端機視窗時啟動：

```bash
python3 outputs/WebCamTraveler_V0.1.12.pyw
```

預設會讀取 `outputs/webcam_sources.json`，並載入 `outputs/guideme24_webcams.json`。目前 GuideMe24 預設來源是直接 YouTube 直播擷取：程式會讀取清單中的 YouTube ID，用 `yt-dlp` 解析直播串流，挑選可用的最佳解析度，再由 OpenCV 擷取當下 frame，轉成 JPG 圖片。啟動時會先抓 5 個畫面，讓輪播先開始；接著背景會繼續抓取其他來源並存檔當緩衝。畫面每 5 秒換下一張。

抓取排程採輪巡模式：每一輪會讓所有攝影機來源都抓取過一次，全部完成後才開始下一輪重新抓取。

介面配置：

- 來源名稱顯示在圖片上方空白區
- 抓取狀況顯示在視窗最下方
- 圖片下方顯示目前來源攝影機網址，並可按「開啟來源」
- 暫停/繼續按鈕在圖片下方
- 圖片下方可直接調整輪播間隔秒數
- 可勾選「視窗頂置」並按「執行」套用
- 可按「縮版 / 控制欄」按鈕收納或展開下方資訊與右方清單
- 圖片會隨視窗大小最大化縮放，視窗可自由調整尺寸
- 可按「永久儲存」把目前顯示圖片複製到 `outputs/Livepic/`
- 歷史 JPG 每 1 小時清理一次，只保留 `latest_*.jpg` 供輪播使用

緩衝擷取到的圖片會存在：

```text
outputs/Temppic/
```

永久儲存按鈕會把目前顯示的圖片存到：

```text
outputs/Livepic/
```

每台攝影機會有兩種檔案：

- `latest_*.jpg`：最新畫面，輪播用
- `*_YYYYMMDD_HHMMSS.jpg`：歷史擷取圖

## 新增世界各地攝影機

若要改用自己的攝影機清單，可編輯 `outputs/webcams.json` 或另外建立 JSON，再用 `--config` 指定：

```json
{
  "name": "Shibuya Crossing",
  "country": "Japan",
  "kind": "auto",
  "url": "https://your-public-webcam-url.example/live.mjpg",
  "interval_seconds": 15
}
```

```bash
python3 outputs/WebCamTraveler_V0.1.12.py --config outputs/webcams.json
```

常用參數：

```bash
python3 outputs/WebCamTraveler_V0.1.12.py --initial-frames 5 --buffer-batch-size 24 --slide-seconds 5
```

- `--initial-frames`：啟動時優先抓幾張
- `--buffer-batch-size`：背景緩衝佇列一次最多維持幾個待抓來源
- `--slide-seconds`：輪播換圖秒數
- `--capture-dir`：暫存圖片資料夾，預設 `outputs/Temppic`
- `--permanent-dir`：永久儲存圖片資料夾，預設 `outputs/Livepic`

支援的來源類型：

- `snapshot`：URL 直接回傳 JPG/PNG/WebP 快照
- `best_snapshot`：從 `image_candidates` 多個快照網址中挑解析度最大的圖片
- `webpage_screenshot`：開啟來源網頁並截取網頁上的直播區域
- `youtube_thumbnail`：用 YouTube ID 抓取最高可用解析度縮圖，可作為非直播備用模式
- `mjpeg`：MJPEG 串流
- `youtube_stream`：YouTube 直播或影片 URL，會解析串流後擷取 frame
- `auto`：程式自動判斷；RTSP/HLS/一般影片串流會交給 OpenCV 嘗試讀取

很多 webcam 網站只給嵌入播放器，不一定提供可直接讀取的影像 URL。若來源需要登入、API key、或禁止轉取，請不要放進設定檔。
