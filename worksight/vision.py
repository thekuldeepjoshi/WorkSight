# Model loading and MediaPipe hand detection for worksight.pipeline.

"""Mandatory model preflight shared by the worker and validation script."""

from worksight.config import ROOT


def load_models(pose_name="yolo26n-pose.pt"):
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


def detect_hands(model, frame, people):
    """Inspect worker and wrist crops, then map landmarks into full-video coordinates."""
    import cv2
    import math
    from types import SimpleNamespace

    height, width = frame.shape[:2]
    landmarks, handedness, wrists = [], [], []
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
                points = [SimpleNamespace(x=(left + p.x * (right - left)) / width,
                                          y=(top + p.y * (bottom - top)) / height,
                                          z=p.z) for p in hand.landmark]
                wrist = (points[0].x * width, points[0].y * height)
                if any(math.hypot(wrist[0] - x, wrist[1] - y) < max(12, (y2 - y1) * .05) for x, y in wrists):
                    continue
                wrists.append(wrist)
                landmarks.append(SimpleNamespace(landmark=points))
                handedness.append(result.multi_handedness[index])
    return SimpleNamespace(multi_hand_landmarks=landmarks, multi_handedness=handedness)
