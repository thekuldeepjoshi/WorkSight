"""Video processing and local storage for the WorkSight prototype."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
PROCESSED_DIR = DATA_DIR / "processed"
DB_PATH = DATA_DIR / "worksight.sqlite3"


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
                error TEXT,
                created_at TEXT NOT NULL,
                processed_at TEXT
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

            CREATE INDEX IF NOT EXISTS detections_video_frame
                ON detections(video_id, frame_index);
            CREATE INDEX IF NOT EXISTS detections_video_track
                ON detections(video_id, track_id);
            CREATE INDEX IF NOT EXISTS events_video_start
                ON events(video_id, start_ms);
            """
        )


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as video_file:
        for chunk in iter(lambda: video_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def inspect_video(path: Path) -> dict:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"Could not open video: {path}")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if width <= 0 or height <= 0:
            raise ValueError("The video does not contain readable frames.")
        capture.set(cv2.CAP_PROP_POS_MSEC, 1000)
        ok, frame = capture.read()
        if not ok:
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = capture.read()
        if not ok:
            raise ValueError("Could not read a frame from this video.")
        if not math.isfinite(fps) or fps <= 0:
            fps = 30.0
        return {
            "width": width,
            "height": height,
            "fps": fps,
            "frame_count": frame_count,
            "duration_seconds": frame_count / fps if frame_count else 0.0,
            "preview": frame,
        }
    finally:
        capture.release()


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
        "work_area_seconds": dwell / 1000,
    }


def _inside_zone(point: tuple[float, float], polygon: np.ndarray) -> bool:
    return cv2.pointPolygonTest(polygon, point, False) >= 0


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
    else:
        details = {
            "rule": "The tracked person's footpoint was inside the work-area zone.",
            "interpretation": "Zone presence, not a measure of productivity.",
        }
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
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Detect, track, estimate pose, and persist frame observations and events."""
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

    belt_polygon = np.asarray(
        [[x * width, y * height] for x, y in zones["conveyor"]], dtype=np.float32
    ).reshape((-1, 1, 2))
    work_polygon = np.asarray(
        [[x * width, y * height] for x, y in zones["work_area"]], dtype=np.float32
    ).reshape((-1, 1, 2))
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    output_path = PROCESSED_DIR / f"{video_id[:16]}_annotated.mp4"
    output_width = width + width % 2
    output_height = height + height % 2
    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (output_width, output_height),
    )
    if not writer.isOpened():
        capture.release()
        raise RuntimeError(f"Could not create annotated video: {output_path}")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    signature = zone_signature(zones)
    db = connect_db()
    db.execute(
        """INSERT INTO videos
           (id, source_name, source_path, status, width, height, fps, frame_count,
            duration_seconds, zone_signature, output_path, error, created_at)
           VALUES (?, ?, ?, 'processing', ?, ?, ?, ?, ?, ?, ?, NULL, ?)
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
               error = NULL,
               processed_at = NULL""",
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
            now,
        ),
    )
    db.execute("DELETE FROM detections WHERE video_id = ?", (video_id,))
    db.execute("DELETE FROM events WHERE video_id = ?", (video_id,))
    db.commit()

    capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
    observation_batch: list[tuple] = []
    event_batch: list[tuple] = []
    active = {"work_area_presence": {}, "possible_belt_interaction": {}}
    thresholds = {
        "work_area_presence": int(minimum_dwell_seconds * 1000),
        "possible_belt_interaction": int(minimum_interaction_seconds * 1000),
    }
    frame_interval_ms = max(1, round(1000 / fps))
    frame_index = 0

    try:
        from ultralytics import YOLO

        model = YOLO(model_name)
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            timestamp_ms = round(frame_index * 1000 / fps)
            result = model.track(
                frame,
                persist=True,
                tracker="bytetrack.yaml",
                conf=confidence,
                verbose=False,
            )[0]
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
            detected: dict[int, tuple[bool, bool, float]] = {}

            for index, (box, score, track_id_value) in enumerate(
                zip(box_values, confidences, track_values)
            ):
                x1, y1, x2, y2 = (float(value) for value in box)
                track_id = int(track_id_value)
                pose = point_values[index].tolist() if index < len(point_values) else []
                footpoint = ((x1 + x2) / 2, min(y2, height - 1))
                in_work_area = _inside_zone(footpoint, work_polygon)
                wrist_on_belt = False
                for wrist_index in (9, 10):
                    if wrist_index >= len(pose):
                        continue
                    wrist_x, wrist_y = pose[wrist_index][:2]
                    wrist_confidence = pose[wrist_index][2] if len(pose[wrist_index]) > 2 else 0
                    if wrist_confidence >= 0.25 and _inside_zone(
                        (float(wrist_x), float(wrist_y)), belt_polygon
                    ):
                        wrist_on_belt = True

                visible_track_id = track_id if track_id >= 0 else None
                observation_batch.append(
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
                if visible_track_id is not None:
                    detected[visible_track_id] = (in_work_area, wrist_on_belt, float(score))

            for event_type, flag_index in (
                ("work_area_presence", 0),
                ("possible_belt_interaction", 1),
            ):
                event_tracks = {
                    track_id
                    for track_id, flags in detected.items()
                    if flags[flag_index]
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
                    score = detected[track_id][2]
                    if track_id not in active[event_type]:
                        active[event_type][track_id] = {
                            "start_ms": timestamp_ms,
                            "last_ms": timestamp_ms,
                            "evidence_frame": frame_index,
                            "confidence": score,
                        }
                    else:
                        state = active[event_type][track_id]
                        state["last_ms"] = timestamp_ms
                        state["confidence"] = max(state["confidence"], score)

            annotated = result.plot()
            cv2.polylines(annotated, [belt_polygon.astype(np.int32)], True, (0, 0, 255), 3)
            cv2.polylines(annotated, [work_polygon.astype(np.int32)], True, (255, 180, 0), 3)
            cv2.putText(
                annotated,
                "Red: conveyor | Blue: work area | wrist proximity is an estimate",
                (16, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                2,
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
        if event_batch:
            db.executemany(
                """INSERT INTO events
                   (video_id, track_id, event_type, start_ms, end_ms, duration_ms,
                    evidence_frame, confidence, details_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                event_batch,
            )
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
        db.close()
