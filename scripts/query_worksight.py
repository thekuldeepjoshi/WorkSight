#!/usr/bin/env python3
"""Read-only JSON queries for WorkSight's local SQLite analysis database."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote


def connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"WorkSight database not found: {path}")
    uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def label_time(milliseconds: int | float) -> str:
    seconds = max(0, int(milliseconds // 1000))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def json_print(data: object) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def table_exists(db: sqlite3.Connection, name: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def list_videos(db: sqlite3.Connection) -> None:
    rows = db.execute(
        """SELECT id, source_name, status, duration_seconds, processed_at,
                  source_path, output_path
           FROM videos ORDER BY created_at DESC"""
    ).fetchall()
    json_print([
        {
            **dict(row),
            "duration_label": label_time((row["duration_seconds"] or 0) * 1000),
            "source_exists": Path(row["source_path"]).is_file(),
            "annotated_video_exists": bool(row["output_path"] and Path(row["output_path"]).is_file()),
        }
        for row in rows
    ])


def select_video(db: sqlite3.Connection, video_id: str | None) -> sqlite3.Row:
    if video_id:
        row = db.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    else:
        row = db.execute(
            "SELECT * FROM videos WHERE status = 'complete' ORDER BY processed_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        raise ValueError("No matching processed video found. Run the 'videos' command and supply a valid --video-id.")
    if row["status"] != "complete":
        raise ValueError(f"Video {row['id']} is not complete (status={row['status']}).")
    return row


def summary(db: sqlite3.Connection, video: sqlite3.Row) -> None:
    video_id = video["id"]
    people, observations = db.execute(
        """SELECT COUNT(DISTINCT track_id), COUNT(*) FROM detections
           WHERE video_id = ? AND track_id IS NOT NULL""",
        (video_id,),
    ).fetchone()
    by_type = db.execute(
        """SELECT event_type, COUNT(*) AS event_count,
                  SUM(duration_ms) AS total_duration_ms,
                  AVG(duration_ms) AS mean_duration_ms,
                  AVG(confidence) AS mean_detection_confidence
           FROM events WHERE video_id = ? GROUP BY event_type ORDER BY event_type""",
        (video_id,),
    ).fetchall()
    zones = db.execute(
        "SELECT name, points_json, updated_at FROM zones WHERE video_id = ? ORDER BY name",
        (video_id,),
    ).fetchall()
    hand_observations = 0
    if table_exists(db, "hand_detections"):
        hand_observations = db.execute(
            "SELECT COUNT(*) FROM hand_detections WHERE video_id = ?", (video_id,)
        ).fetchone()[0]
    package_observations = 0
    conveyor_package_observations = 0
    if table_exists(db, "package_detections"):
        package_observations, conveyor_package_observations = db.execute(
            """SELECT COUNT(*), SUM(in_conveyor_zone) FROM package_detections
               WHERE video_id = ?""", (video_id,)
        ).fetchone()
    json_print({
        "video": {
            "id": video_id,
            "source_name": video["source_name"],
            "duration_seconds": video["duration_seconds"],
            "annotated_video": video["output_path"],
            "annotated_video_exists": bool(video["output_path"] and Path(video["output_path"]).is_file()),
            "processed_at": video["processed_at"],
        },
        "distinct_anonymous_tracks": people,
        "person_frame_observations": observations,
        "hand_frame_observations": hand_observations,
        "tracked_package_observations": package_observations,
        "package_observations_in_conveyor_zone": conveyor_package_observations or 0,
        "events_by_type": [dict(row) for row in by_type],
        "configured_zones": [dict(row) for row in zones],
    })


def frame_counts(db: sqlite3.Connection, video_id: str) -> list[sqlite3.Row]:
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


def timeline(db: sqlite3.Connection, video: sqlite3.Row, args: argparse.Namespace) -> None:
    window_ms = args.window * 1000
    bucket_data: dict[int, dict] = defaultdict(lambda: {
        "frames": 0, "present_sum": 0, "active_sum": 0, "peak_present": 0,
        "peak_candidate_active": 0, "active_frames": 0, "first_ms": None, "last_ms": None,
    })
    for row in frame_counts(db, video["id"]):
        bucket = row["timestamp_ms"] // window_ms
        item = bucket_data[bucket]
        item["frames"] += 1
        item["present_sum"] += row["present_count"]
        item["active_sum"] += row["candidate_active_count"]
        item["peak_present"] = max(item["peak_present"], row["present_count"])
        item["peak_candidate_active"] = max(item["peak_candidate_active"], row["candidate_active_count"])
        item["active_frames"] += row["candidate_active_count"] > 0
        item["first_ms"] = row["timestamp_ms"] if item["first_ms"] is None else item["first_ms"]
        item["last_ms"] = row["timestamp_ms"]

    event_counts = db.execute(
        """SELECT CAST(start_ms / ? AS INTEGER) AS bucket, COUNT(*) AS n
           FROM events WHERE video_id = ?
             AND event_type IN ('possible_belt_interaction', 'possible_hand_belt_proximity')
           GROUP BY bucket""",
        (window_ms, video["id"]),
    ).fetchall()
    for row in event_counts:
        bucket_data[row["bucket"]]["belt_event_count"] = row["n"]

    records = []
    for bucket, item in bucket_data.items():
        n = item["frames"]
        item_out = {
            "start_ms": bucket * window_ms,
            "end_ms": (bucket + 1) * window_ms,
            "start": label_time(bucket * window_ms),
            "end": label_time(min((bucket + 1) * window_ms, (video["duration_seconds"] or 0) * 1000)),
            "tracked_person_observation_frames": n,
            "mean_workers_present": round(item["present_sum"] / n, 3),
            "mean_candidate_active_workers": round(item["active_sum"] / n, 3),
            "peak_workers_present_same_frame": item["peak_present"],
            "peak_candidate_active_same_frame": item["peak_candidate_active"],
            "tracked_person_frames_with_candidate_activity_pct": round(100 * item["active_frames"] / n, 2),
            "possible_belt_events": item.get("belt_event_count", 0),
            "observed_from": label_time(item["first_ms"]),
            "observed_to": label_time(item["last_ms"]),
        }
        records.append(item_out)
    if args.order == "busy":
        records.sort(key=lambda x: (x["mean_candidate_active_workers"], x["peak_candidate_active_same_frame"], x["possible_belt_events"]), reverse=True)
    elif args.order == "quiet":
        records.sort(key=lambda x: (x["tracked_person_frames_with_candidate_activity_pct"], x["mean_candidate_active_workers"]))
    else:
        records.sort(key=lambda x: (x["possible_belt_events"], x["mean_candidate_active_workers"]), reverse=True)
    json_print({
        "video_id": video["id"],
        "source_video": video["source_path"],
        "annotated_video": video["output_path"],
        "window_seconds": args.window,
        "order": args.order,
        "measure_definition": "Counts are distinct tracks per frame, then averaged/peaked within each window. Candidate active means wrist_on_belt=1 or a tracked fingertip landmark inside the conveyor zone; neither confirms unloading.",
        "windows": records[:args.limit],
    })


def imbalances(db: sqlite3.Connection, video: sqlite3.Row, args: argparse.Namespace) -> None:
    window_ms = args.window * 1000
    qualifying: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    all_frames: dict[int, int] = defaultdict(int)
    for row in frame_counts(db, video["id"]):
        bucket = row["timestamp_ms"] // window_ms
        all_frames[bucket] += 1
        if row["present_count"] >= args.min_present and row["candidate_active_count"] <= args.max_active:
            qualifying[bucket].append((row["timestamp_ms"], row["present_count"], row["candidate_active_count"]))

    records = []
    for bucket, frames in qualifying.items():
        if len(frames) < args.min_frames:
            continue
        indices = sorted({0, len(frames) // 2, len(frames) - 1})
        samples = [
            {
                "timestamp_ms": frames[i][0],
                "timestamp": label_time(frames[i][0]),
                "workers_present": frames[i][1],
                "candidate_active_workers": frames[i][2],
            }
            for i in indices
        ]
        records.append({
            "window_start": label_time(bucket * window_ms),
            "window_end": label_time(min((bucket + 1) * window_ms, (video["duration_seconds"] or 0) * 1000)),
            "window_seconds": args.window,
            "present_at_least": args.min_present,
            "candidate_active_at_most": args.max_active,
            "qualifying_frames": len(frames),
            "tracked_person_observation_frames_in_window": all_frames[bucket],
            "percent_of_tracked_person_observation_frames": round(100 * len(frames) / all_frames[bucket], 2),
            "evidence_timestamps": samples,
        })
    records.sort(key=lambda x: (x["percent_of_tracked_person_observation_frames"], x["qualifying_frames"]), reverse=True)
    json_print({
        "video_id": video["id"],
        "source_video": video["source_path"],
        "annotated_video": video["output_path"],
        "interpretation": "Candidate staffing mismatch only: same-frame work-area presence met the threshold while hand/wrist conveyor-zone activity did not. This does not prove idleness.",
        "windows": records[:args.limit],
    })


def events(db: sqlite3.Connection, video: sqlite3.Row, args: argparse.Namespace) -> None:
    query = """SELECT id, track_id, event_type, start_ms, end_ms, duration_ms,
                      evidence_frame, confidence, details_json
               FROM events WHERE video_id = ?"""
    params: list[object] = [video["id"]]
    if args.event_type:
        query += " AND event_type = ?"
        params.append(args.event_type)
    query += " ORDER BY start_ms LIMIT ?"
    params.append(args.limit)
    result = []
    for row in db.execute(query, params):
        item = dict(row)
        item["start"] = label_time(item["start_ms"])
        item["end"] = label_time(item["end_ms"])
        item["details"] = json.loads(item.pop("details_json"))
        result.append(item)
    json_print({"video_id": video["id"], "annotated_video": video["output_path"], "events": result})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/worksight.sqlite3"), help="WorkSight SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("videos", help="List videos and available evidence paths")
    for name, help_text in (
        ("summary", "Summarize one processed video"),
        ("events", "List event intervals"),
        ("timeline", "Rank fixed time windows from frame-level signals"),
        ("imbalances", "Find same-frame presence/activity mismatch windows"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--video-id", help="Video ID; defaults to latest complete video")
        if name == "events":
            command.add_argument(
                "--event-type",
                choices=(
                    "possible_belt_interaction",
                    "possible_hand_belt_proximity",
                    "possible_person_in_conveyor_zone",
                    "possible_body_on_conveyor",
                    "candidate_available_no_belt_demand",
                    "candidate_working_with_package",
                    "candidate_belt_bottleneck",
                    "candidate_waiting_full_belt",
                    "candidate_staffing_pressure",
                    "possible_throwing_risk",
                    "work_area_presence",
                ),
            )
            command.add_argument("--limit", type=int, default=100)
        elif name == "timeline":
            command.add_argument("--window", type=int, default=60, help="Window duration in seconds")
            command.add_argument("--order", choices=("busy", "quiet", "events"), default="busy")
            command.add_argument("--limit", type=int, default=20)
        elif name == "imbalances":
            command.add_argument("--window", type=int, default=30, help="Window duration in seconds")
            command.add_argument("--min-present", type=int, default=3)
            command.add_argument("--max-active", type=int, default=1)
            command.add_argument("--min-frames", type=int, default=3, help="Ignore one-off frame noise")
            command.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    try:
        with connect_readonly(args.db) as db:
            if args.command == "videos":
                list_videos(db)
                return 0
            video = select_video(db, args.video_id)
            if args.command == "summary":
                summary(db, video)
            elif args.command == "events":
                events(db, video, args)
            elif args.command == "timeline":
                if args.window <= 0 or args.limit <= 0:
                    raise ValueError("--window and --limit must be positive.")
                timeline(db, video, args)
            elif args.command == "imbalances":
                if min(args.window, args.min_present, args.min_frames, args.limit) <= 0 or args.max_active < 0:
                    raise ValueError("--window, --min-present, --min-frames, and --limit must be positive; --max-active cannot be negative.")
                imbalances(db, video, args)
        return 0
    except (FileNotFoundError, ValueError, sqlite3.Error) as error:
        print(json.dumps({"error": str(error)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
