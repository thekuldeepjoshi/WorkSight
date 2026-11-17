# WorkSight

WorkSight analyzes unloading videos and turns person, pose, package, and zone detections into reviewable operational signals. Signals are estimates; confirm them in the video before acting.

## Run the analyzer

Python 3.10 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Detailed MediaPipe hand landmarks need the pinned Protobuf 4 dependency. Streamlit is pinned
to 1.60 because later Streamlit releases require Protobuf 5+, which MediaPipe does not support.
Recreate the environment, or reinstall requirements, after pulling this change.

For Intel Macs, use Python 3.12 and `requirements-intel-mac.txt`:

```bash
python3.12 -m venv .venv-intel
./.venv-intel/bin/python -m pip install -r requirements-intel-mac.txt
./.venv-intel/bin/python -m streamlit run app.py
```

Upload or select a video, draw and save the conveyor and work-area zones, then process it. Results are stored in `data/worksight.sqlite3`; annotated videos are in `data/processed/`.

## Run the React dashboard

In another terminal:

```bash
cd dashboard
npm install
npm run dev
```

Upload the exported events CSV and annotated H.264 video. The dashboard validates both files and links review signals to video timestamps.

The SQLite data can be queried without processing the video again:

```bash
python3 scripts/query_worksight.py summary
python3 scripts/query_worksight.py timeline --window 60 --order quiet
```

Video and model files are kept locally in the ignored `data/` and `*.pt` files. Review [Ultralytics licensing](https://www.ultralytics.com/license) before distributing or deploying.
