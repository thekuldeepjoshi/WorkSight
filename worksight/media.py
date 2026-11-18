"""Inspect videos, draw frame annotations, and encode browser-ready exports."""

import math
import subprocess
from pathlib import Path

import cv2
import numpy as np

from worksight.config import HAND_CONNECTIONS


def inspect_video(path: Path) -> dict:
    """Read a video's dimensions, FPS, frame count, and duration."""
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
        return {"width": width, "height": height, "fps": fps, "frame_count": frame_count,
                "duration_seconds": frame_count / fps if frame_count else 0.0, "preview": frame}
    finally:
        capture.release()


def convert_browser_video(source: Path, target: Path) -> None:
    """Convert the annotated output into a browser-friendly MP4."""
    """Encode the OpenCV output as browser-compatible H.264."""
    import imageio_ffmpeg

    temporary_target = target.with_name(f"{target.stem}.tmp.mp4")
    try:
        subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-y", "-i", str(source),
             "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", "-an", str(temporary_target)],
            check=True,
        )
        temporary_target.replace(target)
    except Exception:
        temporary_target.unlink(missing_ok=True)
        raise


def annotate_frame(annotated, people, packages, hands, signals, zones, belt_package_count):
    """Draw zones, tracks, packages, hands, and alerts on one frame."""
    """Draw the zone overlays, package boxes, hand landmarks, and review labels."""
    height, width = annotated.shape[:2]
    for package in packages:
        px1, py1, px2, py2 = (round(value) for value in package["bbox"])
        highlighted_package = package["in_belt"] or package.get("in_hand", False)
        package_color = (42, 92, 165) if highlighted_package else (255, 180, 0)
        package_id = package["track_id"] if package["track_id"] is not None else "?"
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
    cv2.polylines(annotated, [zones["conveyor"].astype(np.int32)], True, (0, 0, 255), 3)
    cv2.polylines(annotated, [zones["safety"].astype(np.int32)], True, (0, 165, 255), 1)
    cv2.polylines(annotated, [zones["work_area"].astype(np.int32)], True, (255, 180, 0), 3)
    cv2.putText(
        annotated,
        f"Red: conveyor | Orange: safety zone | Blue: work area | belt packages: {belt_package_count}",
        (16, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    for hand in hands:
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
        if track_id is not None and signals[track_id]["possible_person_in_conveyor_zone"]:
            x1, y1, _, _ = person["bbox"]
            review_text = (
                "URGENT REVIEW: PERSON ON BELT"
                if signals[track_id]["possible_body_on_conveyor"]
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
    if width % 2 or height % 2:
        annotated = cv2.copyMakeBorder(
            annotated,
            0,
            height % 2,
            0,
            width % 2,
            cv2.BORDER_CONSTANT,
            value=(0, 0, 0),
        )
    return annotated
