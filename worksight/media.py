"""Video inspection and browser-compatible encoding."""

import math
import subprocess
from pathlib import Path

import cv2


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
        return {"width": width, "height": height, "fps": fps, "frame_count": frame_count,
                "duration_seconds": frame_count / fps if frame_count else 0.0, "preview": frame}
    finally:
        capture.release()


def convert_browser_video(source: Path, target: Path) -> None:
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
