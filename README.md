# WorkSight Operations Prototype

Analyze a local operations video with person detection, tracking, and pose estimation. Draw a conveyor zone and a work-area zone, then review anonymous track activity and possible wrist-to-conveyor interactions in a local dashboard.

## Run locally

Python 3.10 or newer is required. From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app.py
```

The first analysis downloads the `yolo26n-pose.pt` model weights. In the app, select `unloding.mp4` or upload a video, draw and save both zones, then process it. Results are saved to `data/worksight.sqlite3`; the annotated video is saved under `data/processed/`.

## What the signals mean

- **Work-area presence**: a tracked person's bottom-center point is inside the configured work-area polygon.
- **Possible belt interaction**: a visible left or right wrist keypoint is inside the conveyor polygon for the configured minimum time.

These are video-derived indicators for review. The pose model estimates body joints, not detailed hand shape, and a wrist inside a zone does not prove a specific task. Track IDs are temporary and anonymous within a video; the dashboard does not identify people or calculate a productivity score.

## Local data

The SQLite database stores per-frame bounding boxes, track IDs, pose keypoints, zone membership, and event intervals. You can query it directly with SQLite without running inference again. Uploaded source videos, the database, model weights, and annotated outputs stay in the ignored `data/` directory.

Before distributing or deploying a product built with Ultralytics, review its AGPL-3.0 and Enterprise licensing options: <https://www.ultralytics.com/license>.
