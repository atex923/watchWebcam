# watchWebcam

`watchWebcam` is a Python desktop app for collecting frames from public webcam sources and showing the latest captures in a Tkinter slideshow.

Current app version:

```text
WebCamTraveler V0.1.12
```

## Run

```bash
python3 -m pip install -r requirements.txt
python3 WebCamTraveler.py
```

The default configuration is:

```text
outputs/webcam_sources.json
```

Captured temporary images are written to `outputs/Temppic/`, and saved images are written to `outputs/Livepic/`. These generated image folders are intentionally ignored by Git.

## Program History

Older generated versions are archived in:

```text
outputs/program_history/
```

The current homepage entry point is `WebCamTraveler.py`. The versioned copy remains in `outputs/WebCamTraveler_V0.1.12.py`, and historical versions remain in `outputs/program_history/`.

More detailed Traditional Chinese usage notes are in:

```text
outputs/world_webcam_viewer_README.md
```
