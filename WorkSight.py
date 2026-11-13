import argparse
from pathlib import Path

import cv2


def process_video(source: Path, destination: Path) -> None:
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise SystemExit(f"Could not open video: {source}")

    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    writer = cv2.VideoWriter(
        str(destination), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
    )
    if not writer.isOpened():
        capture.release()
        raise SystemExit(f"Could not create output video: {destination}")

    subtractor = cv2.createBackgroundSubtractorMOG2(
        history=500, varThreshold=40, detectShadows=True
    )
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break

            mask = subtractor.apply(frame)
            mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)[1]
            contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            count = 0
            for contour in contours:
                if cv2.contourArea(contour) < 500:
                    continue
                x, y, w, h = cv2.boundingRect(contour)
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                count += 1

            cv2.putText(
                frame, f"Moving objects: {count}", (20, 35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2,
            )
            writer.write(frame)
    finally:
        capture.release()
        writer.release()


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect motion in a video.")
    parser.add_argument("video", type=Path, help="Path to an input video")
    parser.add_argument(
        "-o", "--output", type=Path, help="Output path (default: <video>_processed.mp4)"
    )
    args = parser.parse_args()
    output = args.output or args.video.with_name(f"{args.video.stem}_processed.mp4")

    if args.video.resolve() == output.resolve():
        parser.error("input and output paths must be different")
    process_video(args.video, output)
    print(f"Saved processed video to {output}")


if __name__ == "__main__":
    main()
