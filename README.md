# WorkSight Operations Prototype

Analyze a local operations video with person detection, tracking, and pose estimation. Draw a conveyor zone and a work-area zone, then review anonymous track activity and possible wrist-to-conveyor interactions in a local dashboard.

## Run locally

Python 3.10 or newer is required. On Apple Silicon or Linux, from this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

### Intel Mac setup

Your Mac is Intel (`x86_64`). PyTorch stopped publishing Intel macOS wheels after 2.2; Python 3.12 with PyTorch 2.2.2 and TorchVision 0.17.2 is the compatible wheel set used here. Install the Python 3.12 universal2 package from [python.org](https://www.python.org/downloads/release/python-31210/) (3.12.10 was the last 3.12 release with a macOS installer), then open a new terminal and run:

```bash
python3.12 -m venv .venv-intel
source .venv-intel/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-intel-mac.txt
python -m streamlit run app.py
```

The Intel-specific requirements pin PyTorch and TorchVision to their compatible versions and keep NumPy below 2. The Python 3.12 universal2 installer supports both Intel and Apple Silicon Macs. See the [PyTorch macOS x86_64 support notice](https://dev-discuss.pytorch.org/t/pytorch-macos-x86-builds-deprecation-starting-january-2024/1690) and the [PyTorch 2.2.2 wheels](https://pypi.org/project/torch/2.2.2/).

The first analysis downloads the `yolo26n-pose.pt` model weights. In the app, select `unloding.mp4` or upload a video, draw and save both zones, then process it. Results are saved to `data/worksight.sqlite3`; the annotated video is saved under `data/processed/`.

## What the signals mean

- **Work-area presence**: a tracked person's bottom-center point is inside the configured work-area polygon.
- **Possible belt interaction**: a visible left or right wrist keypoint is inside the conveyor polygon for the configured minimum time.

These are video-derived indicators for review. The pose model estimates body joints, not detailed hand shape, and a wrist inside a zone does not prove a specific task. Track IDs are temporary and anonymous within a video; the dashboard does not identify people or calculate a productivity score.

## Local data

The SQLite database stores per-frame bounding boxes, track IDs, pose keypoints, zone membership, and event intervals. You can query it directly with SQLite without running inference again. Uploaded source videos, the database, model weights, and annotated outputs stay in the ignored `data/` directory.

Before distributing or deploying a product built with Ultralytics, review its AGPL-3.0 and Enterprise licensing options: <https://www.ultralytics.com/license>.
