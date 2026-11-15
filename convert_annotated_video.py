"""Convert an OpenCV mp4v annotation into a browser-friendly H.264 MP4."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(
            'Usage: python convert_annotated_video.py "data/processed/video_annotated.mp4"'
        )
    source = Path(sys.argv[1]).expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"Video not found: {source}")
    target = source.with_name(f"{source.stem}_browser.mp4")
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-y",
        "-i",
        str(source),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "22",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-an",
        str(target),
    ]
    subprocess.run(command, check=True)
    print(f"Browser-ready video saved to: {target}")


if __name__ == "__main__":
    main()
