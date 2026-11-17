"""SQLite schema and repository helpers. No model imports."""

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from worksight.config import DATA_DIR, DB_PATH

def connect_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
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
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY REFERENCES videos(id),
                source_video_id TEXT NOT NULL REFERENCES videos(id),
                status TEXT NOT NULL DEFAULT 'queued',
                progress REAL NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
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
    if not all(isinstance(p, (list, tuple)) and len(p) == 2
               and all(isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 1 for v in p)
               for p in points):
        raise ValueError("Zone points must be finite normalized coordinates between 0 and 1.")
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
