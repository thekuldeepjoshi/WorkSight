"""Load the vision models and turn their output into frame detections."""

import math

import cv2

from worksight.config import ROOT


def load_models(pose_name="yolo26n-pose.pt"):
    """Load pose, package, and hand models once for a processing run."""
    from ultralytics import YOLO
    import mediapipe as mp

    hands = None
    try:
        pose_path = ROOT / pose_name
        pose = YOLO(str(pose_path if pose_path.exists() else pose_name))
        packages = YOLO(str(ROOT / "yolov8s-world.pt"))
        packages.set_classes(["box", "package", "carton", "parcel"])
        hands = mp.solutions.hands.Hands(
            static_image_mode=True, max_num_hands=8, model_complexity=1,
            min_detection_confidence=0.3, min_tracking_confidence=0.3,
        )
        return pose, hands, packages
    except Exception as error:
        if hands is not None:
            hands.close()
        raise RuntimeError(f"Mandatory model preflight failed: {error}") from error


def track_objects(model, frame, *, confidence, **options):
    """Detect objects and keep a stable track ID between video frames."""
    """Run YOLO with ByteTrack and unpack boxes, track IDs, and any pose points."""
    result = model.track(
        frame, persist=True, tracker="bytetrack.yaml", conf=confidence,
        verbose=False, **options,
    )[0]
    boxes = result.boxes
    if boxes is None:
        return result, []

    coordinates = boxes.xyxy.cpu().tolist()
    scores = boxes.conf.cpu().tolist()
    track_ids = boxes.id.int().cpu().tolist() if boxes.id is not None else [None] * len(boxes)
    class_ids = boxes.cls.int().cpu().tolist()
    poses = result.keypoints.data.cpu().tolist() if result.keypoints is not None else []
    detections = []
    for index, bbox in enumerate(coordinates):
        track_id = track_ids[index]
        detections.append({
            "track_id": track_id if track_id is not None and track_id >= 0 else None,
            "bbox": tuple(bbox),
            "confidence": scores[index],
            "label": str(result.names.get(class_ids[index], "package")),
            "pose": poses[index] if index < len(poses) else [],
        })
    return result, detections


def detect_hands(model, frame, people):
    """Find hand landmarks inside each visible person's bounding box."""
    """Find hands in worker and wrist crops and map them back to the full frame."""
    height, width = frame.shape[:2]
    hands = []
    wrists = []
    for person in people:
        x1, y1, x2, y2 = person["bbox"]
        pad = max(12, (x2 - x1) * .15)
        crops = [(x1 - pad, y1 - pad, x2 + pad, y2 + pad)]
        for index in (9, 10):
            pose = person["pose"]
            if index < len(pose) and pose[index][2] >= .25:
                x, y = pose[index][:2]
                radius = max(35, (y2 - y1) * .18)
                crops.append((x - radius, y - radius, x + radius, y + radius))
        for left, top, right, bottom in crops:
            left, top = max(0, int(left)), max(0, int(top))
            right, bottom = min(width, int(right)), min(height, int(bottom))
            if right - left < 8 or bottom - top < 8:
                continue
            crop = frame[top:bottom, left:right]
            scale = max(1, 512 / max(crop.shape[:2]))
            if scale > 1:
                crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            result = model.process(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
            for index, hand in enumerate(result.multi_hand_landmarks or []):
                normalized = [
                    ((left + p.x * (right - left)) / width, (top + p.y * (bottom - top)) / height)
                    for p in hand.landmark
                ]
                points = [(x * width, y * height) for x, y in normalized]
                wrist = points[0]
                if any(math.hypot(wrist[0] - x, wrist[1] - y) < max(12, (y2 - y1) * .05) for x, y in wrists):
                    continue
                wrists.append(wrist)
                labels = result.multi_handedness or []
                classifications = labels[index].classification if index < len(labels) else []
                label = classifications[0] if classifications else None
                hands.append({
                    "points": points,
                    "landmarks": [[round(x, 6), round(y, 6)] for x, y in normalized],
                    "handedness": label.label if label else "unknown",
                    "confidence": float(label.score) if label else 0.0,
                })
    return hands
