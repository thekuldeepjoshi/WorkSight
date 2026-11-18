# Small, testable geometry and operational rules used by worksight.pipeline.

"""Zone helpers and operational event rules."""

import math

import cv2
import numpy as np

from worksight.config import HAND_FINGERTIPS


def inside_zone(point: tuple[float, float], polygon: np.ndarray) -> bool:
    """Return true when a point sits inside or on a zone boundary."""
    return cv2.pointPolygonTest(polygon, point, False) >= 0


def inset_zone(polygon: np.ndarray, scale: float = 0.82) -> np.ndarray:
    """Shrink a zone to reduce safety alerts near its outer edge."""
    points = polygon.reshape((-1, 2))
    center = points.mean(axis=0)
    return (center + (points - center) * scale).astype(np.float32).reshape((-1, 1, 2))


def package_activity_signals(conveyor_package_count: int, carrying: bool,
                             hand_signal: bool, toward_package: bool) -> dict[str, bool]:
    """Turn package and hand evidence into simple worker activity cues."""
    belt_has_packages = conveyor_package_count > 0
    working = carrying or (belt_has_packages and (toward_package or hand_signal))
    return {
        "candidate_available_no_belt_demand": not belt_has_packages and not carrying,
        "candidate_working_with_package": working,
        "candidate_belt_bottleneck": belt_has_packages and not carrying and not toward_package and not hand_signal,
        "candidate_waiting_full_belt": conveyor_package_count >= 4 and not hand_signal and not carrying,
    }


def staffing_pressure(package_count: int, active_worker_count: int) -> bool:
    """Flag frames where package demand may need more workers."""
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


def _hand_owner(wrist, people):
    """Find the nearest person whose padded bounding box contains the hand."""
    wrist_x, wrist_y = wrist
    nearest = None
    nearest_distance = math.inf
    for person in people:
        x1, y1, x2, y2 = person["bbox"]
        if not (x1 - 12 <= wrist_x <= x2 + 12 and y1 - 12 <= wrist_y <= y2 + 12):
            continue
        pose = person["pose"]
        wrist_distances = [
            math.hypot(wrist_x - pose[i][0], wrist_y - pose[i][1])
            for i in (9, 10) if i < len(pose) and len(pose[i]) > 2 and pose[i][2] >= 0.2
        ]
        distance = min(wrist_distances) if wrist_distances else math.hypot(
            wrist_x - (x1 + x2) / 2, wrist_y - (y1 + y2) / 2
        )
        if distance < nearest_distance:
            nearest = person
            nearest_distance = distance
    return nearest["track_id"] if nearest else None


def evaluate_frame(people, packages, hands, zones, frame_size, history, *,
                   timestamp_ms, frame_interval_ms, analysis_fps):
    """Update detections with zone evidence and return per-track event signals."""
    width, height = frame_size
    conveyor = zones["conveyor"]
    detected = {}

    for person in people:
        x1, y1, x2, y2 = person["bbox"]
        center = ((x1 + x2) / 2, (y1 + y2) / 2)
        footpoint = (center[0], min(y2, height - 1))
        in_work_area = inside_zone(footpoint, zones["work_area"])
        wrist_on_belt, person_at_belt, body_on_belt = person_belt_signals(
            person["pose"], footpoint, center, conveyor, zones["safety"]
        )
        person.update(center=center, in_work_area=in_work_area, wrist_on_belt=wrist_on_belt)
        track_id = person["track_id"]
        if track_id is None:
            continue
        previous_center = history["worker_centers"].get(track_id)
        history["worker_centers"][track_id] = center
        detected[track_id] = {
            "work_area_presence": in_work_area,
            "possible_belt_interaction": wrist_on_belt,
            "possible_hand_belt_proximity": False,
            "possible_person_in_conveyor_zone": person_at_belt,
            "possible_body_on_conveyor": body_on_belt,
            "confidence": person["confidence"],
            "low_motion": previous_center is not None
            and math.hypot(center[0] - previous_center[0], center[1] - previous_center[1])
            <= max(12.0, width * 0.012),
        }

    for hand in hands:
        track_id = _hand_owner(hand["points"][0], people)
        fingertips_in_conveyor = any(
            point_index < len(hand["points"]) and inside_zone(hand["points"][point_index], conveyor)
            for point_index in HAND_FINGERTIPS
        )
        hand.update(track_id=track_id, fingertips_in_conveyor=fingertips_in_conveyor)
        if track_id is not None and fingertips_in_conveyor:
            detected[track_id]["possible_hand_belt_proximity"] = True

    belt_packages = []
    for package in packages:
        x1, y1, x2, y2 = package["bbox"]
        center = ((x1 + x2) / 2, (y1 + y2) / 2)
        package.update(center=center, in_belt=inside_zone(center, conveyor), in_hand=False)
        if package["in_belt"]:
            belt_packages.append(package)

    # Combine hand proximity, package position, and movement into worker cues.
    active_workers = set()
    previous_distances = history["worker_package_distances"]
    for person in people:
        track_id = person["track_id"]
        if track_id is None:
            continue
        flags = detected[track_id]
        x1, y1, x2, y2 = person["bbox"]
        pose = person["pose"]
        worker_height = y2 - y1
        wrist_points = [pose[i][:2] for i in (9, 10) if i < len(pose) and pose[i][2] >= 0.2]
        hand_points = [point for hand in hands if hand["track_id"] == track_id for point in hand["points"]]
        carrying = False
        for package in packages:
            px1, py1, px2, py2 = package["bbox"]
            overlaps_worker = px1 < x2 and px2 > x1 and py1 < y2 and py2 > y1
            if not overlaps_worker:
                continue
            near_wrist = any(
                math.hypot(package["center"][0] - wx, package["center"][1] - wy)
                < max(32.0, worker_height * 0.24)
                for wx, wy in wrist_points
            )
            near_hand = any(
                math.hypot(max(px1 - hx, 0, hx - px2), max(py1 - hy, 0, hy - py2))
                < max(18.0, worker_height * 0.12)
                for hx, hy in hand_points
            )
            if near_wrist or near_hand:
                carrying = True
                package["in_hand"] = True

        toward_package = False
        if belt_packages:
            wx, wy = person["center"]
            distance = min(
                math.hypot(wx - package["center"][0], wy - package["center"][1])
                for package in belt_packages
            )
            previous = previous_distances.get(track_id)
            toward_package = previous is not None and previous - distance > max(2.0, width * 0.002)
            previous_distances[track_id] = distance
        else:
            previous_distances.pop(track_id, None)

        hand_signal = flags["possible_hand_belt_proximity"]
        flags.update(package_activity_signals(len(belt_packages), carrying, hand_signal, toward_package))
        no_handling = not (flags["possible_belt_interaction"] or hand_signal or carrying or toward_package)
        flags["candidate_low_activity"] = (
            not flags["work_area_presence"] or carrying or (flags["low_motion"] and no_handling)
        )
        if flags["candidate_working_with_package"]:
            active_workers.add(track_id)

    if staffing_pressure(len(belt_packages), len(active_workers)):
        for track_id in active_workers:
            detected[track_id]["candidate_staffing_pressure"] = True

    # Fast package motion near a wrist can trigger a throwing review cue.
    previous_positions = history["package_positions"]
    throwing_until = history["throwing_until"]
    for package in packages:
        track_id = package["track_id"]
        x, y = package["center"]
        previous = previous_positions.get(track_id)
        previous_positions[track_id] = (x, y)
        if previous is None:
            continue
        speed = math.hypot(x - previous[0], y - previous[1]) * analysis_fps
        if speed <= max(width, height) * 0.8:
            continue
        for person in people:
            person_id = person["track_id"]
            if person_id is None:
                continue
            pose = person["pose"]
            distance_limit = max(30.0, (person["bbox"][3] - person["bbox"][1]) * 0.18)
            if any(
                math.hypot(x - pose[i][0], y - pose[i][1]) < distance_limit
                for i in (9, 10) if i < len(pose) and pose[i][2] >= 0.2
            ):
                throwing_until[person_id] = timestamp_ms + frame_interval_ms
    for track_id, flags in detected.items():
        if timestamp_ms <= throwing_until.get(track_id, -1):
            flags["possible_throwing_risk"] = True

    return detected, len(belt_packages)
