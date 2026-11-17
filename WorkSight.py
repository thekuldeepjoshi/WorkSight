"""Video processing and local storage for the WorkSight prototype."""

from __future__ import annotations

import json
import hashlib
import math
import csv
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from worksight.config import (
    ANALYSIS_VERSION,
    DATA_DIR,
    DB_PATH,
    HAND_CONNECTIONS,
    HAND_FINGERTIPS,
    PROCESSED_DIR,
    UPLOADS_DIR,
)
from worksight.media import convert_browser_video, hash_file, inspect_video
from worksight.rules import (
    inside_zone as _inside_zone,
    inset_zone as _inset_zone,
    package_activity_signals as _package_activity_signals,
    staffing_pressure as _staffing_pressure,
)


def connect_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_db() -> None:
    with connect_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS videos (
                id TEXT PRIMARY KEY,
                source_name TEXT NOT NULL,
                source_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ready',
                width INTEGER,
                height INTEGER,
                fps REAL,
                frame_count INTEGER,
                duration_seconds REAL,
                zone_signature TEXT,
                output_path TEXT,
                events_path TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                processed_at TEXT,
                analysis_version INTEGER NOT NULL DEFAULT 1,
                hand_landmarks_enabled INTEGER NOT NULL DEFAULT 0,
                package_detection_enabled INTEGER NOT NULL DEFAULT 0,
                analysis_fps REAL NOT NULL DEFAULT 30
            );

            CREATE TABLE IF NOT EXISTS zones (
                video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                points_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (video_id, name)
            );

            CREATE TABLE IF NOT EXISTS detections (
                id INTEGER PRIMARY KEY,
                video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                frame_index INTEGER NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                track_id INTEGER,
                confidence REAL NOT NULL,
                bbox_json TEXT NOT NULL,
                keypoints_json TEXT NOT NULL,
                in_work_area INTEGER NOT NULL,
                wrist_on_belt INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY,
                video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                track_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                start_ms INTEGER NOT NULL,
                end_ms INTEGER NOT NULL,
                duration_ms INTEGER NOT NULL,
                evidence_frame INTEGER NOT NULL,
                confidence REAL NOT NULL,
                details_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hand_detections (
                id INTEGER PRIMARY KEY,
                video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                frame_index INTEGER NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                track_id INTEGER,
                handedness TEXT,
                handedness_confidence REAL NOT NULL,
                landmarks_json TEXT NOT NULL,
                fingertips_in_conveyor INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS package_detections (
                id INTEGER PRIMARY KEY,
                video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                frame_index INTEGER NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                track_id INTEGER,
                label TEXT NOT NULL,
                confidence REAL NOT NULL,
                bbox_json TEXT NOT NULL,
                in_conveyor_zone INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS detections_video_frame
                ON detections(video_id, frame_index);
            CREATE INDEX IF NOT EXISTS detections_video_track
                ON detections(video_id, track_id);
            CREATE INDEX IF NOT EXISTS events_video_start
                ON events(video_id, start_ms);
            CREATE INDEX IF NOT EXISTS hand_detections_video_frame
                ON hand_detections(video_id, frame_index);
            CREATE INDEX IF NOT EXISTS hand_detections_video_track
                ON hand_detections(video_id, track_id);
            CREATE INDEX IF NOT EXISTS package_detections_video_frame
                ON package_detections(video_id, frame_index);
            """
        )
        video_columns = {row[1] for row in db.execute("PRAGMA table_info(videos)")}
        if "analysis_version" not in video_columns:
            db.execute(
                "ALTER TABLE videos ADD COLUMN analysis_version INTEGER NOT NULL DEFAULT 1"
            )
        if "hand_landmarks_enabled" not in video_columns:
            db.execute(
                "ALTER TABLE videos ADD COLUMN hand_landmarks_enabled INTEGER NOT NULL DEFAULT 0"
            )
        if "package_detection_enabled" not in video_columns:
            db.execute("ALTER TABLE videos ADD COLUMN package_detection_enabled INTEGER NOT NULL DEFAULT 0")
        if "analysis_fps" not in video_columns:
            db.execute("ALTER TABLE videos ADD COLUMN analysis_fps REAL NOT NULL DEFAULT 30")
        if "events_path" not in video_columns:
            db.execute("ALTER TABLE videos ADD COLUMN events_path TEXT")


def ensure_video(video_id: str, source_name: str, source_path: Path) -> None:
    initialize_db()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as db:
        db.execute(
            """INSERT INTO videos (id, source_name, source_path, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   source_name = excluded.source_name,
                   source_path = excluded.source_path""",
            (video_id, source_name, str(source_path), now),
        )


def save_zone(video_id: str, name: str, points: list[list[float]]) -> None:
    if name not in {"conveyor", "work_area"}:
        raise ValueError("Zone must be 'conveyor' or 'work_area'.")
    if len(points) < 3:
        raise ValueError("A zone needs at least three points.")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect_db() as db:
        db.execute(
            """INSERT INTO zones (video_id, name, points_json, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(video_id, name) DO UPDATE SET
                   points_json = excluded.points_json,
                   updated_at = excluded.updated_at""",
            (video_id, name, json.dumps(points), now),
        )


def delete_zone(video_id: str, name: str) -> None:
    with connect_db() as db:
        db.execute("DELETE FROM zones WHERE video_id = ? AND name = ?", (video_id, name))


def get_zones(video_id: str) -> dict[str, list[list[float]]]:
    with connect_db() as db:
        rows = db.execute(
            "SELECT name, points_json FROM zones WHERE video_id = ?", (video_id,)
        ).fetchall()
    return {row["name"]: json.loads(row["points_json"]) for row in rows}


def zone_signature(zones: dict[str, list[list[float]]]) -> str:
    encoded = json.dumps(zones, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def list_videos() -> list[dict]:
    initialize_db()
    with connect_db() as db:
        rows = db.execute(
            """SELECT id, source_name, source_path, status, processed_at
               FROM videos ORDER BY created_at DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def get_video(video_id: str) -> dict | None:
    with connect_db() as db:
        row = db.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    return dict(row) if row else None


def get_events(video_id: str) -> list[dict]:
    with connect_db() as db:
        rows = db.execute(
            """SELECT id, track_id, event_type, start_ms, end_ms, duration_ms,
                      evidence_frame, confidence, details_json
               FROM events WHERE video_id = ? ORDER BY start_ms""",
            (video_id,),
        ).fetchall()
    events = [dict(row) for row in rows]
    for event in events:
        event["details"] = json.loads(event.pop("details_json"))
    return events


def _event_time_label(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _create_run_paths(source_name: str, started_at: datetime) -> tuple[Path, Path, Path]:
    """Create the next numbered output folder and return its video/CSV paths."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    run_numbers = []
    for path in PROCESSED_DIR.iterdir():
        match = re.match(r"^(\d+)_", path.name)
        if path.is_dir() and match:
            run_numbers.append(int(match.group(1)))
    run_number = max(run_numbers, default=0) + 1
    timestamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(source_name).stem)
    prefix = f"{run_number:04d}_{timestamp}_{safe_name}"
    run_dir = PROCESSED_DIR / prefix
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir, run_dir / f"{prefix}_annotated.mp4", run_dir / f"{prefix}_events.csv"


def _write_events_csv(db: sqlite3.Connection, video_id: str, target: Path) -> None:
    rows = db.execute(
        """SELECT track_id, event_type, start_ms, duration_ms, confidence, details_json
           FROM events WHERE video_id = ? ORDER BY start_ms""",
        (video_id,),
    ).fetchall()
    with target.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=["Time", "Track", "Signal", "Duration (s)", "Confidence", "Evidence rule", "Interpretation"],
        )
        writer.writeheader()
        for row in rows:
            details = json.loads(row["details_json"])
            writer.writerow(
                {
                    "Time": _event_time_label(row["start_ms"]),
                    "Track": "Team" if row["track_id"] < 0 else row["track_id"],
                    "Signal": row["event_type"].replace("_", " "),
                    "Duration (s)": round(row["duration_ms"] / 1000, 1),
                    "Confidence": round(row["confidence"], 2),
                    "Evidence rule": details.get("rule", ""),
                    "Interpretation": details.get("interpretation", ""),
                }
            )


def _record_staffing_mismatch_events(
    db: sqlite3.Connection, video_id: str, frame_interval_ms: int
) -> None:
    """Record sustained frames with several present tracks but little belt activity."""
    rows = db.execute(
        """SELECT frame_index, timestamp_ms,
                  COUNT(DISTINCT CASE WHEN in_work_area = 1 THEN track_id END) AS present_count,
                  COUNT(DISTINCT CASE WHEN wrist_on_belt = 1 THEN track_id END) AS active_count
           FROM detections WHERE video_id = ? AND track_id IS NOT NULL
           GROUP BY frame_index, timestamp_ms ORDER BY timestamp_ms""",
        (video_id,),
    ).fetchall()
    state = None

    def insert_state(item):
        duration_ms = item["last_ms"] - item["start_ms"] + frame_interval_ms
        if duration_ms < 1000:
            return
        details = {
            "rule": "At least three tracked people were in the work area while no more than one had a wrist-in-conveyor activity cue in the same frame.",
            "interpretation": "Possible staffing mismatch review cue; confirm the video before inferring idle time or changing staffing.",
        }
        db.execute(
            """INSERT INTO events (video_id, track_id, event_type, start_ms, end_ms,
               duration_ms, evidence_frame, confidence, details_json)
               VALUES (?, -1, 'candidate_staffing_mismatch', ?, ?, ?, ?, 0, ?)""",
            (video_id, item["start_ms"], item["last_ms"], duration_ms, item["frame_index"], json.dumps(details)),
        )

    for row in rows:
        qualifying = row["present_count"] >= 3 and row["active_count"] <= 1
        if qualifying:
            if state is None:
                state = {"start_ms": row["timestamp_ms"], "last_ms": row["timestamp_ms"], "frame_index": row["frame_index"]}
            else:
                state["last_ms"] = row["timestamp_ms"]
        elif state is not None:
            insert_state(state)
            state = None
    if state is not None:
        insert_state(state)


def get_summary(video_id: str) -> dict:
    with connect_db() as db:
        tracks = db.execute(
            """SELECT COUNT(DISTINCT track_id) FROM detections
               WHERE video_id = ? AND track_id IS NOT NULL""",
            (video_id,),
        ).fetchone()[0]
        detections = db.execute(
            "SELECT COUNT(*) FROM detections WHERE video_id = ?", (video_id,)
        ).fetchone()[0]
        belt = db.execute(
            """SELECT COUNT(*), COALESCE(SUM(duration_ms), 0)
               FROM events WHERE video_id = ? AND event_type = 'possible_belt_interaction'""",
            (video_id,),
        ).fetchone()
        hand_belt = db.execute(
            """SELECT COUNT(*) FROM events
               WHERE video_id = ? AND event_type = 'possible_hand_belt_proximity'""",
            (video_id,),
        ).fetchone()[0]
        person_in_conveyor = db.execute(
            """SELECT COUNT(*) FROM events
               WHERE video_id = ? AND event_type = 'possible_person_in_conveyor_zone'""",
            (video_id,),
        ).fetchone()[0]
        hand_observations = db.execute(
            "SELECT COUNT(*) FROM hand_detections WHERE video_id = ?", (video_id,)
        ).fetchone()[0]
        package_observations = db.execute(
            "SELECT COUNT(*) FROM package_detections WHERE video_id = ?", (video_id,)
        ).fetchone()[0]
        conveyor_package_observations = db.execute(
            "SELECT COUNT(*) FROM package_detections WHERE video_id = ? AND in_conveyor_zone = 1", (video_id,)
        ).fetchone()[0]
        dwell = db.execute(
            """SELECT COALESCE(SUM(duration_ms), 0)
               FROM events WHERE video_id = ? AND event_type = 'work_area_presence'""",
            (video_id,),
        ).fetchone()[0]
    return {
        "tracked_people": tracks,
        "observations": detections,
        "belt_interactions": belt[0],
        "belt_interaction_seconds": belt[1] / 1000,
        "hand_belt_proximity_events": hand_belt,
        "person_in_conveyor_zone_events": person_in_conveyor,
        "hand_observations": hand_observations,
        "package_observations": package_observations,
        "conveyor_package_observations": conveyor_package_observations,
        "work_area_seconds": dwell / 1000,
    }


def _insert_event(
    event_batch: list[tuple],
    video_id: str,
    track_id: int,
    event_type: str,
    state: dict,
    end_ms: int,
    frame_interval_ms: int,
    minimum_ms: int,
) -> None:
    duration_ms = max(0, end_ms - state["start_ms"] + frame_interval_ms)
    if duration_ms < minimum_ms:
        return
    if event_type == "possible_belt_interaction":
        details = {
            "rule": "A visible wrist keypoint was inside the conveyor zone.",
            "interpretation": "Potential interaction; review the video before acting.",
        }
    elif event_type == "possible_hand_belt_proximity":
        details = {
            "rule": "At least one MediaPipe fingertip landmark was inside the conveyor zone.",
            "interpretation": "Possible hand-to-conveyor proximity; this does not confirm contact or unloading.",
        }
    elif event_type == "possible_person_in_conveyor_zone":
        details = {
            "rule": "A visible ankle keypoint or the person's footpoint was inside the conveyor zone.",
            "interpretation": "Potential person-in-conveyor-zone safety review; verify the video before calling it a hazard.",
        }
    elif event_type == "possible_body_on_conveyor":
        details = {"rule": "Two or more visible hip, knee, or ankle pose points fell inside the conveyor zone.", "interpretation": "Major-risk review cue for a person on/over the conveyor; pose-zone overlap can be wrong and must be verified."}
    elif event_type == "candidate_available_no_belt_demand":
        details = {"rule": "No package detection was inside the conveyor zone and no package was estimated near the worker's wrist.", "interpretation": "Potentially available for work; verify belt view and package detector coverage."}
    elif event_type == "candidate_working_with_package":
        details = {"rule": "A package was estimated near the worker's wrist, or a belt package and hand-zone/approach signal co-occurred.", "interpretation": "Candidate package handling; a held item means the worker is not classified as available, even while the belt is empty."}
    elif event_type == "candidate_belt_bottleneck":
        details = {"rule": "One or more package detections were in the conveyor zone, and this worker had no carry, hand-zone, or approach signal.", "interpretation": "Possible unloading slowdown; verify occlusion and worker-package association."}
    elif event_type == "candidate_waiting_full_belt":
        details = {"rule": "At least four package detections were on the conveyor while this worker had no hand-zone or package-near-wrist signal.", "interpretation": "Possible waiting during high belt load; item count is a proxy, not calibrated belt occupancy."}
    elif event_type == "candidate_staffing_pressure":
        details = {"rule": "At least three package detections were on the conveyor while two or more tracks showed candidate handling activity.", "interpretation": "Possible staffing pressure; review sustained backlog before adding workers."}
    elif event_type == "candidate_low_activity":
        details = {"rule": "A tracked person was outside the work area, was carrying a package in the work area, or stayed there with little movement and no handling cue.", "interpretation": "Possible slowdown review cue; verify the video before interpreting it as idle time or a delay."}
    elif event_type == "possible_throwing_risk":
        details = {"rule": "A tracked package moved rapidly near an estimated worker wrist.", "interpretation": "Possible throwing event; motion and hand association are heuristic and require video review."}
    else:
        details = {
            "rule": "The tracked person's footpoint was inside the work-area zone.",
            "interpretation": "Zone presence, not a measure of productivity.",
        }
    if event_type in {"candidate_belt_bottleneck", "candidate_waiting_full_belt", "candidate_staffing_pressure"}:
        peak_packages = state.get("max_conveyor_packages")
        if peak_packages is not None:
            details["rule"] += f" Highest sampled belt count in this interval: {peak_packages} packages."
    event_batch.append(
        (
            video_id,
            track_id,
            event_type,
            state["start_ms"],
            end_ms,
            duration_ms,
            state["evidence_frame"],
            state["confidence"],
            json.dumps(details),
        )
    )


def process_video(
    video_id: str,
    source_path: Path,
    zones: dict[str, list[list[float]]],
    source_name: str | None = None,
    model_name: str = "yolo26n-pose.pt",
    confidence: float = 0.25,
    minimum_interaction_seconds: float = 0.7,
    minimum_dwell_seconds: float = 3.0,
    minimum_conveyor_presence_seconds: float = 0.5,
    analysis_fps: float = 10.0,
    enable_hand_landmarks: bool = False,
    enable_package_detection: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Detect, track, estimate pose/hands/packages, and persist operational evidence."""
    initialize_db()
    if not {"conveyor", "work_area"}.issubset(zones):
        raise ValueError("Draw and save both the conveyor and work-area zones first.")

    capture = cv2.VideoCapture(str(source_path))
    if not capture.isOpened():
        raise ValueError(f"Could not open video: {source_path}")

    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if not math.isfinite(fps) or fps <= 0:
        fps = 30.0
    if width <= 0 or height <= 0:
        capture.release()
        raise ValueError("The video does not contain readable frames.")
    if analysis_fps <= 0:
        capture.release()
        raise ValueError("Analysis FPS must be greater than zero.")

    analysis_stride = max(1, round(fps / analysis_fps))
    effective_analysis_fps = fps / analysis_stride

    belt_polygon = np.asarray(
        [[x * width, y * height] for x, y in zones["conveyor"]], dtype=np.float32
    ).reshape((-1, 1, 2))
    inner_belt_polygon = _inset_zone(belt_polygon)
    work_polygon = np.asarray(
        [[x * width, y * height] for x, y in zones["work_area"]], dtype=np.float32
    ).reshape((-1, 1, 2))
    run_started_at = datetime.now(timezone.utc)
    run_dir, output_path, events_path = _create_run_paths(
        source_name or source_path.name, run_started_at
    )
    intermediate_path = run_dir / "annotated_working.mp4"
    output_width = width + width % 2
    output_height = height + height % 2
    writer = cv2.VideoWriter(
        str(intermediate_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        effective_analysis_fps,
        (output_width, output_height),
    )
    if not writer.isOpened():
        capture.release()
        raise RuntimeError(f"Could not create annotated video: {output_path}")

    now = run_started_at.isoformat(timespec="seconds")
    signature = zone_signature(zones)
    db = connect_db()
    db.execute(
        """INSERT INTO videos
           (id, source_name, source_path, status, width, height, fps, frame_count,
            duration_seconds, zone_signature, output_path, events_path, error, created_at,
            analysis_version, hand_landmarks_enabled, package_detection_enabled, analysis_fps)
           VALUES (?, ?, ?, 'processing', ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
               source_path = excluded.source_path,
               status = 'processing',
               width = excluded.width,
               height = excluded.height,
               fps = excluded.fps,
               frame_count = excluded.frame_count,
               duration_seconds = excluded.duration_seconds,
               zone_signature = excluded.zone_signature,
               output_path = excluded.output_path,
               events_path = excluded.events_path,
               error = NULL,
               processed_at = NULL,
               analysis_version = excluded.analysis_version,
               hand_landmarks_enabled = excluded.hand_landmarks_enabled,
               package_detection_enabled = excluded.package_detection_enabled,
               analysis_fps = excluded.analysis_fps""",
        (
            video_id,
            source_name or source_path.name,
            str(source_path),
            width,
            height,
            fps,
            frame_count,
            frame_count / fps if frame_count else 0.0,
            signature,
            str(output_path),
            str(events_path),
            now,
            ANALYSIS_VERSION,
            int(enable_hand_landmarks),
            int(enable_package_detection),
            effective_analysis_fps,
        ),
    )
    db.execute("DELETE FROM detections WHERE video_id = ?", (video_id,))
    db.execute("DELETE FROM events WHERE video_id = ?", (video_id,))
    db.execute("DELETE FROM hand_detections WHERE video_id = ?", (video_id,))
    db.execute("DELETE FROM package_detections WHERE video_id = ?", (video_id,))
    db.commit()

    capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
    observation_batch: list[tuple] = []
    hand_batch: list[tuple] = []
    package_batch: list[tuple] = []
    event_batch: list[tuple] = []
    active = {
        "work_area_presence": {},
        "possible_belt_interaction": {},
        "possible_hand_belt_proximity": {},
        "possible_person_in_conveyor_zone": {},
        "possible_body_on_conveyor": {},
        "candidate_available_no_belt_demand": {},
        "candidate_working_with_package": {},
        "candidate_belt_bottleneck": {},
        "candidate_waiting_full_belt": {},
        "candidate_staffing_pressure": {},
        "candidate_low_activity": {},
        "possible_throwing_risk": {},
    }
    thresholds = {
        "work_area_presence": int(minimum_dwell_seconds * 1000),
        "possible_belt_interaction": int(minimum_interaction_seconds * 1000),
        "possible_hand_belt_proximity": int(minimum_interaction_seconds * 1000),
        "possible_person_in_conveyor_zone": int(minimum_conveyor_presence_seconds * 1000),
        "possible_body_on_conveyor": int(minimum_conveyor_presence_seconds * 1000),
        "candidate_available_no_belt_demand": 1000,
        "candidate_working_with_package": 700,
        "candidate_belt_bottleneck": 1500,
        "candidate_waiting_full_belt": 1500,
        "candidate_staffing_pressure": 1500,
        "candidate_low_activity": 1000,
        "possible_throwing_risk": 300,
    }
    frame_interval_ms = max(1, round(1000 / effective_analysis_fps))
    frame_index = 0
    processed_frame_index = 0
    previous_package_positions: dict[int, tuple[float, float]] = {}
    previous_worker_package_distance: dict[int, float] = {}
    previous_worker_centers: dict[int, tuple[float, float]] = {}
    throwing_signal_until_ms: dict[int, int] = {}

    hands_model = None
    try:
        from ultralytics import YOLO

        if enable_hand_landmarks:
            try:
                import mediapipe as mp
            except ImportError as error:
                raise RuntimeError(
                    "Detailed hand landmarks require MediaPipe. Install the project requirements "
                    "with Python 3.12, or disable hand landmarks and use wrist-only signals."
                ) from error
            try:
                hands_model = mp.solutions.hands.Hands(
                    static_image_mode=False,
                    max_num_hands=8,
                    model_complexity=1,
                    min_detection_confidence=0.3,
                    min_tracking_confidence=0.3,
                )
            except Exception as error:
                raise RuntimeError(
                    "MediaPipe is installed but could not start its hand model. On macOS, run "
                    "Streamlit from a normal logged-in Terminal session so it can create its "
                    "graphics context, or disable detailed hand landmarks and use wrist-only signals."
                ) from error
        model = YOLO(model_name)
        package_model = None
        if enable_package_detection:
            try:
                package_model = YOLO("yolov8s-world.pt")
                package_model.set_classes(["box", "package", "carton", "parcel"])
            except Exception as error:
                raise RuntimeError(
                    "Package model could not load. Download yolov8s-world.pt and its CLIP "
                    "files with network access, then run the analysis again. No incomplete "
                    f"worker-only result was saved. Model error: {error}"
                ) from error
        # Packages are small and frequently occluded. Run the package model on
        # every analysis frame rather than reusing a stale box for several frames.
        package_frame_stride = 1
        cached_packages: list[dict] = []
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            timestamp_ms = round(frame_index * 1000 / fps)
            if frame_index % analysis_stride:
                if frame_index % 100 == 0:
                    db.commit()
                    if progress:
                        progress(frame_index, frame_count)
                frame_index += 1
                continue
            result = model.track(
                frame,
                persist=True,
                tracker="bytetrack.yaml",
                conf=confidence,
                verbose=False,
            )[0]
            package_result = package_model.track(
                frame,
                persist=True,
                tracker="bytetrack.yaml",
                conf=0.12,
                iou=0.45,
                imgsz=960,
                verbose=False,
            )[0] if package_model is not None and processed_frame_index % package_frame_stride == 0 else None
            if package_result is not None and package_result.boxes is not None:
                cached_packages = []
                for package_box, package_score, package_id, package_class in zip(
                    package_result.boxes.xyxy.cpu().numpy(),
                    package_result.boxes.conf.cpu().numpy(),
                    package_result.boxes.id.int().cpu().numpy() if package_result.boxes.id is not None else np.full((len(package_result.boxes),), -1),
                    package_result.boxes.cls.int().cpu().numpy(),
                ):
                    px1, py1, px2, py2 = map(float, package_box)
                    center = ((px1 + px2) / 2, (py1 + py2) / 2)
                    in_belt = _inside_zone(center, belt_polygon)
                    package_track = int(package_id)
                    package_name = str(package_result.names.get(int(package_class), "package"))
                    cached_packages.append({"track_id": package_track, "bbox": (px1, py1, px2, py2), "center": center, "in_belt": in_belt, "in_hand": False, "label": package_name, "score": float(package_score)})
                    package_batch.append((video_id, frame_index, timestamp_ms, package_track if package_track >= 0 else None, package_name, float(package_score), json.dumps([px1, py1, px2, py2]), int(in_belt)))
            elif package_result is not None:
                cached_packages = []
            packages = cached_packages
            for package in packages:
                package["in_hand"] = False
            boxes = result.boxes
            keypoints = result.keypoints
            box_values = boxes.xyxy.cpu().numpy() if boxes is not None else np.empty((0, 4))
            confidences = boxes.conf.cpu().numpy() if boxes is not None else np.empty((0,))
            track_values = (
                boxes.id.int().cpu().numpy()
                if boxes is not None and boxes.id is not None
                else np.full((len(box_values),), -1)
            )
            point_values = (
                keypoints.data.cpu().numpy()
                if keypoints is not None
                else np.empty((0, 0, 3))
            )
            detected: dict[int, dict[str, bool | float]] = {}
            people: list[dict] = []
            frame_observations: list[tuple] = []

            for index, (box, score, track_id_value) in enumerate(
                zip(box_values, confidences, track_values)
            ):
                x1, y1, x2, y2 = (float(value) for value in box)
                track_id = int(track_id_value)
                pose = point_values[index].tolist() if index < len(point_values) else []
                footpoint = ((x1 + x2) / 2, min(y2, height - 1))
                body_center = ((x1 + x2) / 2, (y1 + y2) / 2)
                in_work_area = _inside_zone(footpoint, work_polygon)
                wrist_on_belt = False
                ankle_points = []
                for wrist_index in (9, 10):
                    if wrist_index >= len(pose):
                        continue
                    wrist_x, wrist_y = pose[wrist_index][:2]
                    wrist_confidence = pose[wrist_index][2] if len(pose[wrist_index]) > 2 else 0
                    if wrist_confidence >= 0.25 and _inside_zone(
                        (float(wrist_x), float(wrist_y)), belt_polygon
                    ):
                        wrist_on_belt = True
                for ankle_index in (15, 16):
                    if ankle_index >= len(pose):
                        continue
                    ankle_x, ankle_y = pose[ankle_index][:2]
                    ankle_confidence = pose[ankle_index][2] if len(pose[ankle_index]) > 2 else 0
                    if ankle_confidence >= 0.25:
                        ankle_points.append((float(ankle_x), float(ankle_y)))
                person_in_conveyor_zone = (
                    any(_inside_zone(point, belt_polygon) for point in ankle_points)
                    if ankle_points
                    else _inside_zone(footpoint, belt_polygon)
                )
                inner_ankles = []
                torso_or_knee_in_inner_zone = False
                for body_index in (11, 12, 13, 14, 15, 16):
                    if body_index >= len(pose) or pose[body_index][2] < 0.5:
                        continue
                    point = (float(pose[body_index][0]), float(pose[body_index][1]))
                    in_inner_belt = _inside_zone(point, inner_belt_polygon)
                    if body_index in (15, 16):
                        inner_ankles.append(in_inner_belt)
                    elif in_inner_belt:
                        torso_or_knee_in_inner_zone = True
                possible_body_on_conveyor = (
                    len(inner_ankles) == 2
                    and all(inner_ankles)
                    and torso_or_knee_in_inner_zone
                    and _inside_zone(body_center, inner_belt_polygon)
                )

                visible_track_id = track_id if track_id >= 0 else None
                low_motion = False
                if visible_track_id is not None:
                    previous_center = previous_worker_centers.get(visible_track_id)
                    low_motion = previous_center is not None and math.hypot(
                        body_center[0] - previous_center[0],
                        body_center[1] - previous_center[1],
                    ) <= max(12.0, width * 0.012)
                    previous_worker_centers[visible_track_id] = body_center
                frame_observations.append(
                    (
                        video_id,
                        frame_index,
                        timestamp_ms,
                        visible_track_id,
                        float(score),
                        json.dumps([x1, y1, x2, y2]),
                        json.dumps(pose),
                        int(in_work_area),
                        int(wrist_on_belt),
                    )
                )
                people.append(
                    {
                        "track_id": visible_track_id,
                        "bbox": (x1, y1, x2, y2),
                        "pose": pose,
                        "score": float(score),
                    }
                )
                if visible_track_id is not None:
                    detected[visible_track_id] = {
                        "work_area_presence": in_work_area,
                        "possible_belt_interaction": wrist_on_belt,
                        "possible_hand_belt_proximity": False,
                        "possible_person_in_conveyor_zone": person_in_conveyor_zone,
                        "possible_body_on_conveyor": possible_body_on_conveyor,
                        "confidence": float(score),
                        "candidate_available_no_belt_demand": False,
                        "candidate_working_with_package": False,
                        "candidate_belt_bottleneck": False,
                        "candidate_waiting_full_belt": False,
                        "candidate_staffing_pressure": False,
                        "candidate_low_activity": False,
                        "possible_throwing_risk": False,
                        "low_motion": low_motion,
                    }

            frame_hands: list[dict] = []
            if hands_model is not None:
                # Hands occupy a small portion of this wide unloading view. Upscaling
                # before inference improves landmark recall while normalized landmarks
                # still map correctly back to the original frame dimensions.
                hand_frame = cv2.resize(
                    frame, None, fx=1.75, fy=1.75, interpolation=cv2.INTER_CUBIC
                )
                hand_result = hands_model.process(cv2.cvtColor(hand_frame, cv2.COLOR_BGR2RGB))
                landmarks_by_hand = hand_result.multi_hand_landmarks or []
                handedness_by_hand = hand_result.multi_handedness or []
                for hand_index, hand_landmarks in enumerate(landmarks_by_hand):
                    points_px = [
                        (float(point.x * width), float(point.y * height))
                        for point in hand_landmarks.landmark
                    ]
                    wrist_x, wrist_y = points_px[0]
                    containing_people = [
                        person for person in people
                        if person["bbox"][0] - 12 <= wrist_x <= person["bbox"][2] + 12
                        and person["bbox"][1] - 12 <= wrist_y <= person["bbox"][3] + 12
                    ]
                    person = None
                    if containing_people:
                        def association_distance(candidate: dict) -> float:
                            pose = candidate["pose"]
                            wrist_distances = []
                            for pose_wrist_index in (9, 10):
                                if pose_wrist_index >= len(pose):
                                    continue
                                pose_wrist = pose[pose_wrist_index]
                                pose_confidence = pose_wrist[2] if len(pose_wrist) > 2 else 0
                                if pose_confidence >= 0.2:
                                    wrist_distances.append(
                                        math.hypot(wrist_x - pose_wrist[0], wrist_y - pose_wrist[1])
                                    )
                            if wrist_distances:
                                return min(wrist_distances)
                            x1, y1, x2, y2 = candidate["bbox"]
                            return math.hypot(wrist_x - (x1 + x2) / 2, wrist_y - (y1 + y2) / 2)

                        person = min(containing_people, key=association_distance)
                    track_id = person["track_id"] if person else None
                    landmarks_normalized = [
                        [round(point.x, 6), round(point.y, 6)]
                        for point in hand_landmarks.landmark
                    ]
                    fingertips_in_conveyor = any(
                        point_index < len(points_px)
                        and _inside_zone(points_px[point_index], belt_polygon)
                        for point_index in HAND_FINGERTIPS
                    )
                    handedness = "unknown"
                    hand_confidence = 0.0
                    if hand_index < len(handedness_by_hand):
                        classifications = handedness_by_hand[hand_index].classification
                        if classifications:
                            handedness = classifications[0].label
                            hand_confidence = float(classifications[0].score)
                    hand_batch.append(
                        (
                            video_id,
                            frame_index,
                            timestamp_ms,
                            track_id,
                            handedness,
                            hand_confidence,
                            json.dumps(landmarks_normalized),
                            int(fingertips_in_conveyor),
                        )
                    )
                    frame_hands.append(
                        {"points": points_px, "fingertips_in_conveyor": fingertips_in_conveyor, "track_id": track_id}
                    )
                    if track_id is not None and fingertips_in_conveyor:
                        detected[track_id]["possible_hand_belt_proximity"] = True

            belt_packages = [package for package in packages if package["in_belt"]]
            active_worker_ids: set[int] = set()
            for person in people:
                track_id = person["track_id"]
                if track_id is None or track_id not in detected:
                    continue
                x1, y1, x2, y2 = person["bbox"]
                worker_center = ((x1 + x2) / 2, (y1 + y2) / 2)
                wrist_points = []
                for wrist_index in (9, 10):
                    pose = person["pose"]
                    if wrist_index < len(pose) and pose[wrist_index][2] >= 0.2:
                        wrist_points.append((float(pose[wrist_index][0]), float(pose[wrist_index][1])))
                carrying = False
                hand_points = [
                    point
                    for hand in frame_hands
                    if hand.get("track_id") == track_id
                    for point in hand["points"]
                ]
                for package in packages:
                    px1, py1, px2, py2 = package["bbox"]
                    package_distance_to_hand = min(
                        (
                            math.hypot(
                                max(px1 - hx, 0, hx - px2),
                                max(py1 - hy, 0, hy - py2),
                            )
                            for hx, hy in hand_points
                        ),
                        default=float("inf"),
                    )
                    near_wrist = any(
                        math.hypot(package["center"][0] - wx, package["center"][1] - wy)
                        < max(32.0, (y2 - y1) * 0.24)
                        for wx, wy in wrist_points
                    )
                    near_hand_landmark = package_distance_to_hand < max(18.0, (y2 - y1) * 0.12)
                    overlaps_worker = px1 < x2 and px2 > x1 and py1 < y2 and py2 > y1
                    if overlaps_worker and (near_wrist or near_hand_landmark):
                        carrying = True
                        package["in_hand"] = True
                toward_package = False
                if belt_packages:
                    distance = min(math.hypot(worker_center[0] - p["center"][0], worker_center[1] - p["center"][1]) for p in belt_packages)
                    prior_distance = previous_worker_package_distance.get(track_id)
                    toward_package = prior_distance is not None and prior_distance - distance > max(2.0, width * 0.002)
                    previous_worker_package_distance[track_id] = distance
                else:
                    previous_worker_package_distance.pop(track_id, None)
                hand_signal = bool(detected[track_id]["possible_hand_belt_proximity"])
                package_signals = _package_activity_signals(
                    enable_package_detection,
                    len(belt_packages),
                    carrying,
                    hand_signal,
                    toward_package,
                )
                detected[track_id].update(package_signals)
                in_work_area = bool(detected[track_id]["work_area_presence"])
                low_motion_without_handling = bool(
                    in_work_area
                    and detected[track_id]["low_motion"]
                    and not detected[track_id]["possible_belt_interaction"]
                    and not hand_signal
                    and not carrying
                    and not toward_package
                )
                detected[track_id]["candidate_low_activity"] = bool(
                    not in_work_area
                    or (in_work_area and carrying)
                    or low_motion_without_handling
                )
                if carrying or (belt_packages and (toward_package or hand_signal)):
                    active_worker_ids.add(track_id)
            if _staffing_pressure(len(belt_packages), len(active_worker_ids)):
                for worker_id in active_worker_ids:
                    detected[worker_id]["candidate_staffing_pressure"] = True

            for package in packages:
                package_id = package["track_id"]
                previous = previous_package_positions.get(package_id)
                if previous is not None:
                    speed = math.hypot(package["center"][0] - previous[0], package["center"][1] - previous[1]) * (effective_analysis_fps / package_frame_stride)
                    if speed > max(width, height) * 0.8:
                        for person in people:
                            track_id = person["track_id"]
                            pose = person["pose"]
                            if track_id is None or track_id not in detected:
                                continue
                            near_hand = any(
                                math.hypot(package["center"][0] - pose[wi][0], package["center"][1] - pose[wi][1]) < max(30.0, (person["bbox"][3]-person["bbox"][1])*0.18)
                                for wi in (9, 10) if wi < len(pose) and pose[wi][2] >= 0.2
                            )
                            if near_hand:
                                throwing_signal_until_ms[track_id] = timestamp_ms + round(package_frame_stride * 1000 / effective_analysis_fps)
                previous_package_positions[package_id] = package["center"]
            for track_id in list(detected):
                if timestamp_ms <= throwing_signal_until_ms.get(track_id, -1):
                    detected[track_id]["possible_throwing_risk"] = True

            observation_batch.extend(frame_observations)

            for event_type in active:
                event_tracks = {
                    track_id
                    for track_id, flags in detected.items()
                    if flags.get(event_type, False)
                }
                for track_id, state in list(active[event_type].items()):
                    if track_id not in event_tracks:
                        _insert_event(
                            event_batch,
                            video_id,
                            track_id,
                            event_type,
                            state,
                            state["last_ms"],
                            frame_interval_ms,
                            thresholds[event_type],
                        )
                        del active[event_type][track_id]
                for track_id in event_tracks:
                    score = float(detected[track_id]["confidence"])
                    if track_id not in active[event_type]:
                        active[event_type][track_id] = {
                            "start_ms": timestamp_ms,
                            "last_ms": timestamp_ms,
                            "evidence_frame": frame_index,
                            "confidence": score,
                            "max_conveyor_packages": len(belt_packages),
                        }
                    else:
                        state = active[event_type][track_id]
                        state["last_ms"] = timestamp_ms
                        state["confidence"] = max(state["confidence"], score)
                        state["max_conveyor_packages"] = max(state.get("max_conveyor_packages", 0), len(belt_packages))

            annotated = result.plot()
            for package in packages:
                px1, py1, px2, py2 = (round(value) for value in package["bbox"])
                highlighted_package = package["in_belt"] or package.get("in_hand", False)
                package_color = (42, 92, 165) if highlighted_package else (255, 180, 0)
                package_id = package["track_id"] if package["track_id"] >= 0 else "?"
                package_label = f"PACKAGE {package_id}"
                if package.get("in_hand", False):
                    package_label += " · HAND"
                cv2.rectangle(annotated, (px1, py1), (px2, py2), package_color, 2)
                cv2.putText(
                    annotated,
                    package_label,
                    (px1, max(18, py1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    package_color,
                    2,
                    cv2.LINE_AA,
                )
            cv2.polylines(annotated, [belt_polygon.astype(np.int32)], True, (0, 0, 255), 3)
            cv2.polylines(annotated, [inner_belt_polygon.astype(np.int32)], True, (0, 165, 255), 1)
            cv2.polylines(annotated, [work_polygon.astype(np.int32)], True, (255, 180, 0), 3)
            cv2.putText(
                annotated,
                f"Red: conveyor | Orange: safety zone | Blue: work area | belt packages: {len(belt_packages)}",
                (16, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            for hand in frame_hands:
                hand_color = (40, 230, 120) if hand["fingertips_in_conveyor"] else (0, 205, 255)
                for first, second in HAND_CONNECTIONS:
                    if max(first, second) < len(hand["points"]):
                        cv2.line(
                            annotated,
                            tuple(round(value) for value in hand["points"][first]),
                            tuple(round(value) for value in hand["points"][second]),
                            hand_color,
                            2,
                            cv2.LINE_AA,
                        )
                for point in hand["points"]:
                    cv2.circle(
                        annotated,
                        tuple(round(value) for value in point),
                        3,
                        hand_color,
                        -1,
                        cv2.LINE_AA,
                    )
            for person in people:
                track_id = person["track_id"]
                if track_id is not None and detected[track_id]["possible_person_in_conveyor_zone"]:
                    x1, y1, _, _ = person["bbox"]
                    review_text = (
                        "URGENT REVIEW: PERSON ON BELT"
                        if detected[track_id]["possible_body_on_conveyor"]
                        else "REVIEW: FOOT IN CONVEYOR ZONE"
                    )
                    review_position = (round(x1), min(height - 12, round(y1) + 28))
                    cv2.putText(
                        annotated,
                        review_text,
                        review_position,
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.78,
                        (0, 0, 0),
                        6,
                        cv2.LINE_AA,
                    )
                    cv2.putText(
                        annotated,
                        review_text,
                        review_position,
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.78,
                        (0, 0, 255),
                        3,
                        cv2.LINE_AA,
                    )
            if output_width != width or output_height != height:
                annotated = cv2.copyMakeBorder(
                    annotated,
                    0,
                    output_height - height,
                    0,
                    output_width - width,
                    cv2.BORDER_CONSTANT,
                    value=(0, 0, 0),
                )
            writer.write(annotated)

            if len(observation_batch) >= 500:
                db.executemany(
                    """INSERT INTO detections
                       (video_id, frame_index, timestamp_ms, track_id, confidence,
                        bbox_json, keypoints_json, in_work_area, wrist_on_belt)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    observation_batch,
                )
                observation_batch.clear()
            if len(hand_batch) >= 500:
                db.executemany(
                    """INSERT INTO hand_detections
                       (video_id, frame_index, timestamp_ms, track_id, handedness,
                        handedness_confidence, landmarks_json, fingertips_in_conveyor)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    hand_batch,
                )
                hand_batch.clear()
            if len(package_batch) >= 500:
                db.executemany(
                    """INSERT INTO package_detections
                       (video_id, frame_index, timestamp_ms, track_id, label,
                        confidence, bbox_json, in_conveyor_zone)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    package_batch,
                )
                package_batch.clear()
            if event_batch:
                db.executemany(
                    """INSERT INTO events
                       (video_id, track_id, event_type, start_ms, end_ms, duration_ms,
                        evidence_frame, confidence, details_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    event_batch,
                )
                event_batch.clear()
            if frame_index % 100 == 0:
                db.commit()
                if progress:
                    progress(frame_index, frame_count)
            processed_frame_index += 1
            frame_index += 1

        end_ms = round(frame_index * 1000 / fps)
        for event_type, tracks in active.items():
            for track_id, state in tracks.items():
                _insert_event(
                    event_batch,
                    video_id,
                    track_id,
                    event_type,
                    state,
                    min(state["last_ms"], end_ms),
                    frame_interval_ms,
                    thresholds[event_type],
                )
        if observation_batch:
            db.executemany(
                """INSERT INTO detections
                   (video_id, frame_index, timestamp_ms, track_id, confidence,
                    bbox_json, keypoints_json, in_work_area, wrist_on_belt)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                observation_batch,
            )
        if hand_batch:
            db.executemany(
                """INSERT INTO hand_detections
                   (video_id, frame_index, timestamp_ms, track_id, handedness,
                    handedness_confidence, landmarks_json, fingertips_in_conveyor)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                hand_batch,
            )
        if package_batch:
            db.executemany(
                """INSERT INTO package_detections
                   (video_id, frame_index, timestamp_ms, track_id, label,
                    confidence, bbox_json, in_conveyor_zone)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                package_batch,
            )
        if event_batch:
            db.executemany(
                """INSERT INTO events
                   (video_id, track_id, event_type, start_ms, end_ms, duration_ms,
                    evidence_frame, confidence, details_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                event_batch,
            )
        writer.release()
        convert_browser_video(intermediate_path, output_path)
        intermediate_path.unlink(missing_ok=True)
        _record_staffing_mismatch_events(db, video_id, frame_interval_ms)
        _write_events_csv(db, video_id, events_path)
        completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.execute(
            """UPDATE videos SET status = 'complete', frame_count = ?,
               duration_seconds = ?, processed_at = ?, error = NULL WHERE id = ?""",
            (frame_index, frame_index / fps, completed_at, video_id),
        )
        db.commit()
        if progress:
            progress(frame_index, max(frame_count, frame_index))
        return output_path
    except Exception as error:
        db.rollback()
        db.execute(
            "UPDATE videos SET status = 'failed', error = ? WHERE id = ?",
            (str(error)[:1000], video_id),
        )
        db.commit()
        raise
    finally:
        capture.release()
        writer.release()
        if hands_model is not None:
            hands_model.close()
        db.close()
