"""Read and write event CSVs for the dashboard and other tools."""
import csv
import json
import math
import sqlite3
from pathlib import Path


def read_events(path):
    """Read an event CSV and normalize values for the dashboard."""
    events = []
    with Path(path).open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        names = {name.strip().lower(): name for name in (reader.fieldnames or [])}
        def column(*aliases):
            return next((names[a] for a in aliases if a in names), None)
        time = column("start seconds", "start timestamp in seconds", "time", "timestamp", "start")
        signal = column("signal", "event type", "event_type")
        track = column("track", "person track id", "track id")
        duration = column("duration (s)", "duration", "duration seconds")
        end = column("end seconds")
        confidence = column("confidence", "score")
        if not all((time, signal, track, confidence)) or not (duration or end):
            raise ValueError("CSV requires time, signal, track, confidence, and duration or end seconds.")
        for index, row in enumerate(reader, 2):
            try:
                raw = row[time].strip()
                parts = raw.split(":")
                seconds = float(raw) if len(parts) == 1 else sum(float(p) * 60 ** i for i, p in enumerate(reversed(parts)))
                length = float(row[end]) - seconds if end else float(row[duration])
                score = float(row[confidence].replace("%", ""))
                # Imported confidence may be 0-100%; convert it to the application's 0.00-1.00 scale.
                if "%" in row[confidence] or score > 1:
                    score /= 100
                kind = row[signal].strip().lower().replace("_", " ")
                # Stored event confidence must stay between the minimum 0.00 and maximum 1.00.
                if not kind or not all(math.isfinite(n) for n in (seconds, length, score)) or seconds < 0 or length <= 0 or not 0 <= score <= 1:
                    raise ValueError("Invalid time, duration, signal, or confidence")
                def optional(name):
                    return row.get(column(name), "") or ""
                events.append(dict(id=len(events) + 1, seconds=seconds, duration=length,
                    label=f"{int(seconds)//60:02d}:{int(seconds)%60:02d}",
                    track=row[track] or "Team", signal=kind, confidence=score,
                    evidence=optional("evidence rule"), interpretation=optional("interpretation"),
                    run_id=optional("run id"), source_name=optional("video")))
            except (ValueError, TypeError, AttributeError, KeyError) as error:
                raise ValueError(f"Invalid CSV row {index}: {error}") from error
    return sorted(events, key=lambda e: e["seconds"])


def _event_time_label(milliseconds: int) -> str:
    """Format milliseconds as a readable video timestamp."""
    seconds = max(0, milliseconds // 1000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def write_events_csv(db: sqlite3.Connection, video_id: str, target: Path) -> None:
    """Export one analysis event list for sharing or dashboard import."""
    rows = db.execute(
        """SELECT id, track_id, event_type, start_ms, end_ms, evidence_frame, duration_ms, confidence, details_json
           FROM events WHERE video_id = ? ORDER BY start_ms""",
        (video_id,),
    ).fetchall()
    video = db.execute("SELECT source_name, fps FROM videos WHERE id=?", (video_id,)).fetchone()
    with target.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=["Time", "Track", "Signal", "Duration (s)", "Confidence", "Evidence rule", "Interpretation",
                        "Schema version", "Run ID", "Video", "Event ID", "Start seconds", "End seconds",
                        "Start frame", "End frame", "Package track ID", "Zone"],
        )
        writer.writeheader()
        for row in rows:
            details = json.loads(row["details_json"])
            writer.writerow(
                {
                    "Time": _event_time_label(row["start_ms"]),
                    "Track": "Team" if row["track_id"] < 0 else row["track_id"],
                    "Signal": row["event_type"].replace("_", " "),
                    "Duration (s)": round(row["duration_ms"] / 1000, 3),
                    "Confidence": round(row["confidence"], 2),
                    "Evidence rule": details.get("rule", ""),
                    "Interpretation": details.get("interpretation", ""),
                    "Schema version": 2,
                    "Run ID": video_id,
                    "Video": video["source_name"] if video else "",
                    "Event ID": row["id"],
                    "Start seconds": row["start_ms"] / 1000,
                    "End seconds": (row["start_ms"] + row["duration_ms"]) / 1000,
                    "Start frame": row["evidence_frame"],
                    "End frame": round(row["end_ms"] * video["fps"] / 1000) if video and video["fps"] else "",
                    "Package track ID": "",
                    "Zone": "work_area" if row["event_type"] == "work_area_presence" else "",
                }
            )
