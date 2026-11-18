# Parses exported event CSV files, including legacy columns, into dashboard-ready event records.

"""Portable events CSV parsing. Legacy names and precise numeric seconds supported."""
import csv
import math
from pathlib import Path


def read_events(path):
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
                if "%" in row[confidence] or score > 1:
                    score /= 100
                kind = row[signal].strip().lower().replace("_", " ")
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
