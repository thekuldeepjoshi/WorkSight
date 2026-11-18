# Small, testable geometry and operational rules used by worksight.pipeline.

"""Zone helpers and operational event rules."""

import cv2
import numpy as np


def inside_zone(point: tuple[float, float], polygon: np.ndarray) -> bool:
    return cv2.pointPolygonTest(polygon, point, False) >= 0


def inset_zone(polygon: np.ndarray, scale: float = 0.82) -> np.ndarray:
    points = polygon.reshape((-1, 2))
    center = points.mean(axis=0)
    return (center + (points - center) * scale).astype(np.float32).reshape((-1, 1, 2))


def package_activity_signals(conveyor_package_count: int, carrying: bool,
                             hand_signal: bool, toward_package: bool) -> dict[str, bool]:
    belt_has_packages = conveyor_package_count > 0
    working = carrying or (belt_has_packages and (toward_package or hand_signal))
    return {
        "candidate_available_no_belt_demand": not belt_has_packages and not carrying,
        "candidate_working_with_package": working,
        "candidate_belt_bottleneck": belt_has_packages and not carrying and not toward_package and not hand_signal,
        "candidate_waiting_full_belt": conveyor_package_count >= 4 and not hand_signal and not carrying,
    }


def staffing_pressure(package_count: int, active_worker_count: int) -> bool:
    return package_count >= 3 and active_worker_count >= 2


def person_belt_signals(pose, footpoint, body_center, belt_polygon, inner_belt_polygon):
    """Return wrist activity, lower-body presence, and stronger body-overlap cues."""
    wrist_on_belt = False
    lower_body_points = [footpoint]
    for wrist_index in (9, 10):
        if wrist_index >= len(pose):
            continue
        wrist_x, wrist_y = pose[wrist_index][:2]
        wrist_confidence = pose[wrist_index][2] if len(pose[wrist_index]) > 2 else 0
        if wrist_confidence >= 0.25 and inside_zone(
            (float(wrist_x), float(wrist_y)), belt_polygon
        ):
            wrist_on_belt = True
    for ankle_index in (15, 16):
        if ankle_index >= len(pose):
            continue
        ankle_x, ankle_y = pose[ankle_index][:2]
        ankle_confidence = pose[ankle_index][2] if len(pose[ankle_index]) > 2 else 0
        if ankle_confidence >= 0.45:
            point = (float(ankle_x), float(ankle_y))
            lower_body_points.append(point)
    for lower_index in (13, 14):
        if lower_index >= len(pose) or pose[lower_index][2] < 0.45:
            continue
        lower_body_points.append((float(pose[lower_index][0]), float(pose[lower_index][1])))
    lower_body_hits = sum(inside_zone(point, inner_belt_polygon) for point in lower_body_points)
    person_in_conveyor_zone = lower_body_hits >= 2
    inner_ankles = []
    torso_or_knee_in_inner_zone = False
    for body_index in (11, 12, 13, 14, 15, 16):
        if body_index >= len(pose) or pose[body_index][2] < 0.5:
            continue
        point = (float(pose[body_index][0]), float(pose[body_index][1]))
        in_inner_belt = inside_zone(point, inner_belt_polygon)
        if body_index in (15, 16):
            inner_ankles.append(in_inner_belt)
        elif in_inner_belt:
            torso_or_knee_in_inner_zone = True
    possible_body_on_conveyor = (
        len(inner_ankles) == 2
        and all(inner_ankles)
        and torso_or_knee_in_inner_zone
        and inside_zone(body_center, inner_belt_polygon)
    )
    return wrist_on_belt, person_in_conveyor_zone, possible_body_on_conveyor
