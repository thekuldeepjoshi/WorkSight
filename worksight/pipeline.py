# Main computer-vision pipeline: reads video frames, runs pose/hand/package models, writes SQLite evidence, annotated video, and CSV events.
# It imports model loading from worksight.vision, persistence from worksight.storage, media helpers from worksight.media, and rules from worksight.rules.

"""Video processing and local storage for the WorkSight prototype."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from worksight.config import ANALYSIS_VERSION, PROCESSED_DIR
from worksight.csv_io import write_events_csv
from worksight.media import annotate_frame, convert_browser_video
from worksight.rules import evaluate_frame, inset_zone
from worksight.storage import connect_db, flush_evidence, frame_counts, initialize_db, zone_signature
from worksight.vision import detect_hands, load_models, track_objects


# Output-storage helper: creates an incremental timestamped folder and names the annotated MP4 and events CSV.
# Dependency: PROCESSED_DIR comes from worksight.config; called at the start of process_video.
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


# Analytics post-processing: compares same-frame worker presence and belt activity to create staffing-mismatch intervals.
# Dependency: detections were inserted by process_video; output is inserted into the events table.
def _record_staffing_mismatch_events(
    db: sqlite3.Connection, video_id: str, frame_interval_ms: int
) -> None:
    """Record sustained frames with several present tracks but little belt activity."""
    rows = frame_counts(db, video_id)
    state = None

    def insert_state(item):
        """Write one sustained staffing mismatch interval."""
        duration_ms = item["last_ms"] - item["start_ms"] + frame_interval_ms
        if duration_ms < 1000:
            return
        details = {
            "rule": "At least three tracked people were in the work area while no more than one track had a wrist or fingertip in the conveyor zone in the same frame.",
            "interpretation": "Possible staffing mismatch review cue; confirm the video before inferring idle time or changing staffing.",
        }
        db.execute(
            """INSERT INTO events (video_id, track_id, event_type, start_ms, end_ms,
               duration_ms, evidence_frame, confidence, details_json)
               VALUES (?, -1, 'candidate_staffing_mismatch', ?, ?, ?, ?, 0, ?)""",
            (video_id, item["start_ms"], item["last_ms"], duration_ms, item["frame_index"], json.dumps(details)),
        )

    for row in rows:
        qualifying = row["present_count"] >= 3 and row["candidate_active_count"] <= 1
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


# Event builder: converts an active per-track signal state into one normalized event tuple after its duration threshold is met.
# Dependency: process_video collects event states, and csv_io.write_events_csv exports saved events.
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
    """Save a completed signal as an event when it lasted long enough."""
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
            "rule": "At least two confident lower-body points (footpoint, ankle, or knee) were inside the inset conveyor zone for at least one second.",
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


# Main pipeline entry point: validates input, samples frames at analysis_fps, runs all models, tracks signals, writes evidence, and finalizes outputs.
# Dependencies: OpenCV reads/writes video; worksight.vision supplies YOLO/MediaPipe; worksight.rules supplies zone/rule helpers; worksight.storage supplies SQLite.
def process_video(
    video_id: str,
    source_path: Path,
    zones: dict[str, list[list[float]]],
    source_name: str | None = None,
    model_name: str = "yolo26n-pose.pt",
    confidence: float = 0.25,
    minimum_interaction_seconds: float = 0.7,
    minimum_dwell_seconds: float = 3.0,
    minimum_conveyor_presence_seconds: float = 1.0,
    analysis_fps: float = 10.0,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Detect, track, estimate pose/hands/packages, and persist operational evidence."""
    # Ensure the SQLite schema exists before any run metadata or observations are written.
    initialize_db()
    if not {"conveyor", "work_area"}.issubset(zones):
        raise ValueError("Draw and save both the conveyor and work-area zones first.")

    # Open the source video through OpenCV and read its dimensions, native FPS, and frame count.
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

    # Convert requested analysis FPS into a frame stride; all expensive models run only on sampled frames.
    analysis_stride = max(1, round(fps / analysis_fps))
    effective_analysis_fps = fps / analysis_stride

    # Convert normalized dashboard zone points into pixel polygons used for all spatial rules.
    pixel_zones = {}
    for name in ("conveyor", "work_area"):
        points = [(x * width, y * height) for x, y in zones[name]]
        pixel_zones[name] = np.asarray(points, dtype=np.float32).reshape(-1, 1, 2)
    pixel_zones["safety"] = inset_zone(pixel_zones["conveyor"])
    try:
        # Load mandatory pose, package, and hand models before creating output evidence.
        models = load_models(model_name)
    except Exception:
        capture.release()
        raise
    # Allocate this run's numbered output folder before processing frames.
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
    # Open the SQLite connection used for this run's metadata, observations, and events.
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
            1,
            1,
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
    # Minimum durations prevent one-frame detections from becoming operational events.
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
    active = {event_type: {} for event_type in thresholds}
    frame_interval_ms = max(1, round(1000 / effective_analysis_fps))
    frame_index = 0
    history = {
        "worker_centers": {},
        "worker_package_distances": {},
        "package_positions": {},
        "throwing_until": {},
    }

    hands_model = None
    try:
        pose_model, hands_model, package_model = models
        while True:
            ok, frame = capture.read()
            if not ok:
                break

            if frame_index % analysis_stride == 0:
                timestamp_ms = round(frame_index * 1000 / fps)
                result, people = track_objects(pose_model, frame, confidence=confidence)
                _, packages = track_objects(package_model, frame, confidence=0.12, iou=0.45, imgsz=960)
                hands = detect_hands(hands_model, frame, people)

                signals, belt_package_count = evaluate_frame(
                    people, packages, hands, pixel_zones, (width, height), history,
                    timestamp_ms=timestamp_ms, frame_interval_ms=frame_interval_ms,
                    analysis_fps=effective_analysis_fps,
                )

                # Close signals that disappeared and extend signals that remain visible across sampled frames.
                for event_type, track_states in active.items():
                    event_tracks = {
                        track_id
                        for track_id, flags in signals.items()
                        if flags.get(event_type, False)
                    }
                    for track_id, state in list(track_states.items()):
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
                            del track_states[track_id]
                    for track_id in event_tracks:
                        score = signals[track_id]["confidence"]
                        if track_id not in track_states:
                            track_states[track_id] = {
                                "start_ms": timestamp_ms,
                                "last_ms": timestamp_ms,
                                "evidence_frame": frame_index,
                                "confidence": score,
                                "max_conveyor_packages": belt_package_count,
                            }
                        else:
                            state = track_states[track_id]
                            state["last_ms"] = timestamp_ms
                            state["confidence"] = max(state["confidence"], score)
                            state["max_conveyor_packages"] = max(state.get("max_conveyor_packages", 0), belt_package_count)

                annotated = annotate_frame(
                    result.plot(), people, packages, hands, signals, pixel_zones, belt_package_count
                )
                writer.write(annotated)

                # Observations record what was seen. Events record cues that lasted long enough.
                frame_key = (video_id, frame_index, timestamp_ms)
                observation_batch.extend(
                    (*frame_key, person["track_id"], person["confidence"], json.dumps(person["bbox"]),
                     json.dumps(person["pose"]), int(person["in_work_area"]), int(person["wrist_on_belt"]))
                    for person in people
                )
                hand_batch.extend(
                    (*frame_key, hand["track_id"], hand["handedness"], hand["confidence"],
                     json.dumps(hand["landmarks"]), int(hand["fingertips_in_conveyor"]))
                    for hand in hands
                )
                package_batch.extend(
                    (*frame_key, package["track_id"], package["label"], package["confidence"],
                     json.dumps(package["bbox"]), int(package["in_belt"]))
                    for package in packages
                )
                flush_evidence(db, observation_batch, hand_batch, package_batch, event_batch)

            if frame_index % 100 == 0:
                db.commit()
                if progress:
                    progress(frame_index, frame_count)
            frame_index += 1

        # Finalize any signals still active at end-of-video, then persist remaining batches.
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
        flush_evidence(db, observation_batch, hand_batch, package_batch, event_batch, final=True)
        # Encode the intermediate annotation, convert it to browser-compatible MP4, and export CSV evidence.
        writer.release()
        convert_browser_video(intermediate_path, output_path)
        intermediate_path.unlink(missing_ok=True)
        _record_staffing_mismatch_events(db, video_id, frame_interval_ms)
        write_events_csv(db, video_id, events_path)
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
    # Any failure marks the analysis as failed and rolls back the current SQLite transaction.
    except Exception as error:
        db.rollback()
        db.execute(
            "UPDATE videos SET status = 'failed', error = ? WHERE id = ?",
            (str(error)[:1000], video_id),
        )
        db.commit()
        raise
    # Always release OpenCV, MediaPipe, and SQLite resources even when processing fails.
    finally:
        capture.release()
        writer.release()
        if hands_model is not None:
            hands_model.close()
        db.close()
