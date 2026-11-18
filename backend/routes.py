# Flask API endpoints for uploads, zones, analysis jobs, imported files, and output downloads.

"""HTTP endpoints for uploads, editable zones, jobs and portable analyses."""
import io
import json
import math
from pathlib import Path
from uuid import uuid4
from flask import Blueprint, current_app, jsonify, request, send_file
from werkzeug.utils import secure_filename

from worksight.config import DATA_DIR, ROOT, UPLOADS_DIR
from worksight.storage import ensure_video, get_video, get_zones, list_videos, save_zones
from worksight.media import inspect_video
from worksight.csv_io import read_events

api = Blueprint("api", __name__)


def required_video(video_id):
    video = get_video(video_id)
    if not video:
        raise LookupError("Unknown video ID")
    return video


def store_file(field, folder, extensions):
    file = request.files.get(field)
    if not file or not file.filename or Path(file.filename).suffix.lower() not in extensions:
        raise ValueError(f"Select a valid {field} file ({', '.join(extensions)})")
    target = folder / (secure_filename(file.filename) or f"{field}.bin")
    file.save(target)
    if target.stat().st_size == 0:
        raise ValueError(f"The {field} file is empty")
    return target


def analysis_response(video, csv_path, video_url, csv_url):
    events = read_events(csv_path)
    metadata = inspect_video(Path(video["source_path"]))
    latest = max((e["seconds"] + e["duration"] for e in events), default=0)
    if latest > metadata["duration_seconds"] + 1:
        raise ValueError("CSV timestamps exceed the selected video duration. Select the matching pair.")
    warnings = []
    names = {e["source_name"] for e in events if e["source_name"]}
    if len(names) > 1 or len({e["run_id"] for e in events if e["run_id"]}) > 1:
        raise ValueError("CSV contains multiple analyses. Export a single run.")
    if names and not any(Path(n).stem.lower() in Path(video["source_name"]).stem.lower() for n in names):
        warnings.append("CSV source name differs from the selected video; verify the pair.")
    return dict(id=video["id"], video_name=video["source_name"], csv_name=Path(csv_path).name,
        duration=metadata["duration_seconds"], events=events, video_url=video_url, csv_url=csv_url,
        warnings=warnings, detailed_metrics="Not available for CSV-only imports")


@api.get("/health")
def health():
    import importlib.util
    return jsonify(status="ok", models={"pose_weights": (ROOT / "yolo26n-pose.pt").is_file(),
        "package_weights": (ROOT / "yolov8s-world.pt").is_file(),
        "mediapipe_installed": importlib.util.find_spec("mediapipe") is not None,
        "note": "File availability only. Every processing run performs actual model preflight."})


@api.get("/videos")
def videos():
    return jsonify(list_videos())


@api.post("/videos")
def upload():
    video_id = uuid4().hex
    folder = UPLOADS_DIR / video_id
    folder.mkdir(parents=True)
    path = store_file("video", folder, {".mp4", ".mov", ".webm", ".avi"})
    metadata = inspect_video(path)
    ensure_video(video_id, path.name, path)
    return jsonify(id=video_id, name=path.name, width=metadata["width"], height=metadata["height"], duration=metadata["duration_seconds"], zones={}), 201


@api.get("/videos/<video_id>/frame")
def preview_frame(video_id):
    import cv2
    video = required_video(video_id)
    capture = cv2.VideoCapture(video["source_path"])
    try:
        second = float(request.args.get("seconds", 0))
        if not math.isfinite(second) or second < 0:
            raise ValueError("Invalid preview time")
        capture.set(cv2.CAP_PROP_POS_MSEC, second * 1000)
        ok, frame = capture.read()
        if not ok:
            raise ValueError("Could not read frame at this time")
        ok, jpeg = cv2.imencode(".jpg", frame)
        if not ok:
            raise ValueError("Could not encode preview")
        return send_file(io.BytesIO(jpeg.tobytes()), mimetype="image/jpeg")
    finally:
        capture.release()


@api.route("/videos/<video_id>/zones", methods=["GET", "PUT"])
def zones(video_id):
    required_video(video_id)
    if request.method == "PUT":
        save_zones(video_id, request.get_json())
    return jsonify(get_zones(video_id))


@api.post("/videos/<video_id>/runs")
def start_run(video_id):
    fps = float((request.get_json() or {}).get("analysis_fps", 5))
    if fps not in (5, 10, 15, 30):
        raise ValueError("Choose 5, 10, 15 or 30 FPS")
    return jsonify(id=current_app.extensions["jobs"].start(video_id, fps)), 202


@api.get("/runs/<run_id>")
def run_status(run_id):
    return jsonify(current_app.extensions["jobs"].status(run_id))


@api.post("/runs/<run_id>/pause")
def pause_run(run_id):
    current_app.extensions["jobs"].pause(run_id)
    return jsonify(current_app.extensions["jobs"].status(run_id))


@api.post("/runs/<run_id>/resume")
def resume_run(run_id):
    current_app.extensions["jobs"].resume(run_id)
    return jsonify(current_app.extensions["jobs"].status(run_id))


@api.post("/runs/<run_id>/cancel")
def cancel_run(run_id):
    current_app.extensions["jobs"].cancel(run_id)
    return jsonify(current_app.extensions["jobs"].status(run_id))


@api.get("/analyses/<video_id>")
def analysis(video_id):
    video = required_video(video_id)
    if video["status"] != "complete":
        raise ValueError("Analysis is not complete")
    return jsonify(analysis_response(video, video["events_path"], f"/api/analyses/{video_id}/video", f"/api/analyses/{video_id}/csv"))


@api.get("/analyses/<video_id>/<kind>")
def output(video_id, kind):
    video = required_video(video_id)
    if kind not in ("video", "csv") or video["status"] != "complete":
        raise LookupError("Output unavailable")
    path = video["output_path" if kind == "video" else "events_path"]
    if not path or not Path(path).is_file():
        raise LookupError("Output file is missing")
    return send_file(path, conditional=True, as_attachment=kind == "csv" or request.args.get("download") == "1")


def import_folder(analysis_id):
    if len(analysis_id) != 32 or any(c not in "0123456789abcdef" for c in analysis_id):
        raise LookupError("Unknown import ID")
    return DATA_DIR / "dashboard_imports" / analysis_id


@api.post("/imports")
def import_analysis():
    analysis_id = uuid4().hex
    folder = import_folder(analysis_id)
    folder.mkdir(parents=True)
    video = store_file("video", folder, {".mp4", ".mov", ".webm"})
    csv_path = store_file("csv", folder, {".csv"})
    info = dict(id=analysis_id, source_name=video.name, source_path=str(video))
    result = analysis_response(info, csv_path, f"/api/imports/{analysis_id}/video", f"/api/imports/{analysis_id}/csv")
    (folder / "analysis.json").write_text(json.dumps({**info, "events_path": str(csv_path)}))
    return jsonify(result), 201


@api.get("/imports")
def imports():
    base = DATA_DIR / "dashboard_imports"
    records = []
    if base.exists():
        for file in sorted(base.glob("*/analysis.json"), reverse=True):
            info = json.loads(file.read_text())
            records.append(dict(id=info["id"], name=info["source_name"]))
    return jsonify(records)


@api.get("/imports/<analysis_id>")
def imported(analysis_id):
    metadata = import_folder(analysis_id) / "analysis.json"
    if not metadata.is_file():
        raise LookupError("Unknown import ID")
    info = json.loads(metadata.read_text())
    return jsonify(analysis_response(info, info["events_path"], f"/api/imports/{analysis_id}/video", f"/api/imports/{analysis_id}/csv"))


@api.get("/imports/<analysis_id>/<kind>")
def imported_output(analysis_id, kind):
    metadata = import_folder(analysis_id) / "analysis.json"
    if not metadata.is_file():
        raise LookupError("Unknown import ID")
    info = json.loads(metadata.read_text())
    if kind not in ("video", "csv"):
        raise LookupError("Unknown file type")
    return send_file(info["source_path" if kind == "video" else "events_path"], conditional=True,
                     as_attachment=kind == "csv" or request.args.get("download") == "1")
