# SQLite schema and repository functions for videos, zones, detections, packages, hands, events, and runs.

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

def save_zones(video_id: str, zones: dict) -> None:
    """Validate both polygons and write them in a single transaction."""
    if not isinstance(zones, dict):
        raise ValueError("Provide the conveyor and work-area zones.")
    rows = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for name in ("conveyor", "work_area"):
        points = zones.get(name)
        if not isinstance(points, list) or len(points) < 3 or not all(
            isinstance(point, (list, tuple)) and len(point) == 2
            and all(isinstance(v, (int, float)) and 0 <= v <= 1 and math.isfinite(v) for v in point)
            for point in points
        ):
            raise ValueError(f"{name} requires at least three normalized points")
        rows.append((video_id, name, json.dumps(points), now))
    with connect_db() as db:
        db.executemany(
            """INSERT INTO zones (video_id, name, points_json, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(video_id, name) DO UPDATE SET
                   points_json = excluded.points_json,
                   updated_at = excluded.updated_at""",
            rows,
        )

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

def open_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"WorkSight database not found: {path}")
    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def table_exists(db: sqlite3.Connection, name: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def frame_counts(db: sqlite3.Connection, video_id: str) -> list[sqlite3.Row]:
    """Count same-frame presence and wrist or fingertip cues for each video."""
    hand_candidate_sql = "0"
    if table_exists(db, "hand_detections"):
        hand_candidate_sql = """EXISTS (
            SELECT 1 FROM hand_detections h
            WHERE h.video_id = d.video_id AND h.frame_index = d.frame_index
              AND h.track_id = d.track_id AND h.fingertips_in_conveyor = 1
        )"""
    return db.execute(
        f"""SELECT d.frame_index, d.timestamp_ms,
                   COUNT(DISTINCT CASE WHEN d.in_work_area = 1 THEN d.track_id END) AS present_count,
                   COUNT(DISTINCT CASE WHEN d.wrist_on_belt = 1 OR {hand_candidate_sql}
                                       THEN d.track_id END) AS candidate_active_count
            FROM detections d
            WHERE d.video_id = ? AND d.track_id IS NOT NULL
            GROUP BY d.frame_index, d.timestamp_ms ORDER BY d.timestamp_ms""",
        (video_id,),
    ).fetchall()


def flush_evidence(db, observation_batch, hand_batch, package_batch, event_batch, *, final=False):
    """Write ready batches during processing and all remaining rows at the end."""
    batches = (
        (observation_batch, 500,
         """INSERT INTO detections
            (video_id, frame_index, timestamp_ms, track_id, confidence,
            bbox_json, keypoints_json, in_work_area, wrist_on_belt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"""),
        (hand_batch, 500,
         """INSERT INTO hand_detections
            (video_id, frame_index, timestamp_ms, track_id, handedness,
            handedness_confidence, landmarks_json, fingertips_in_conveyor)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""),
        (package_batch, 500,
         """INSERT INTO package_detections
            (video_id, frame_index, timestamp_ms, track_id, label,
            confidence, bbox_json, in_conveyor_zone)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""),
        (event_batch, 1,
         """INSERT INTO events
            (video_id, track_id, event_type, start_ms, end_ms, duration_ms,
            evidence_frame, confidence, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"""),
    )
    for rows, batch_size, statement in batches:
        if rows and (final or len(rows) >= batch_size):
            db.executemany(statement, rows)
            rows.clear()
