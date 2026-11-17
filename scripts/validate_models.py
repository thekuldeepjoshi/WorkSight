#!/usr/bin/env python3
"""Actual model and pipeline smoke test in an isolated temporary database."""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--seconds", type=float, default=5)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "model_validation")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    # Set isolation before importing application modules.
    os.environ["WORKSIGHT_DATA_DIR"] = str(args.output.resolve())
    os.environ["WORKSIGHT_DB"] = str((args.output / "validation.sqlite3").resolve())
    import cv2
    from uuid import uuid4
    from WorkSight import process_video
    from worksight.storage import connect_db

    capture = cv2.VideoCapture(str(args.video.resolve()))
    if not capture.isOpened():
        raise ValueError("Cannot open validation video")
    fps = capture.get(cv2.CAP_PROP_FPS)
    size = (int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    capture.set(cv2.CAP_PROP_POS_MSEC, args.start * 1000)
    clip = args.output / f"sample_{uuid4().hex}.mp4"
    writer = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    for _ in range(int(args.seconds * fps)):
        ok, frame = capture.read()
        if not ok:
            break
        writer.write(frame)
    writer.release(); capture.release()
    run_id = uuid4().hex
    zone = [[0, 0], [1, 0], [1, 1], [0, 1]]
    output = process_video(run_id, clip, {"conveyor": zone, "work_area": zone}, analysis_fps=5)
    with connect_db() as db:
        counts = {}
        for table in ("detections", "hand_detections", "package_detections"):
            row = db.execute(f"SELECT COUNT(*),COUNT(DISTINCT track_id),COUNT(DISTINCT frame_index) FROM {table} WHERE video_id=?", (run_id,)).fetchone()
            counts[table] = dict(observations=row[0], tracks=row[1], frames=row[2])
        hands = [json.loads(r[0]) for r in db.execute("SELECT landmarks_json FROM hand_detections WHERE video_id=?", (run_id,))]
        valid_hands = bool(hands) and all(len(h) == 21 and all(0 <= p[0] <= 1 and 0 <= p[1] <= 1 for p in h) for h in hands)
        persistent = {}
        for table in ("detections", "package_detections"):
            persistent[table] = db.execute(f"SELECT COUNT(*) FROM (SELECT track_id FROM {table} WHERE video_id=? AND track_id IS NOT NULL GROUP BY track_id HAVING COUNT(DISTINCT frame_index)>1)", (run_id,)).fetchone()[0]
    result = dict(counts=counts, hand_landmarks_valid=valid_hands, persistent_tracks=persistent,
                  annotated_video=str(output), note="Full-frame test polygons validate wiring, not operational labels. Visually inspect the annotated sample.")
    print(json.dumps(result, indent=2))
    if not valid_hands or any(not count for count in persistent.values()):
        raise SystemExit("Model evidence incomplete on this sample. Select representative footage with visible hands/packages and investigate misses.")


if __name__ == "__main__":
    main()
