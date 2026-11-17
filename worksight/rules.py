"""Zone helpers and operational event rules."""

import cv2
import numpy as np


def inside_zone(point: tuple[float, float], polygon: np.ndarray) -> bool:
    return cv2.pointPolygonTest(polygon, point, False) >= 0


def inset_zone(polygon: np.ndarray, scale: float = 0.82) -> np.ndarray:
    points = polygon.reshape((-1, 2))
    center = points.mean(axis=0)
    return (center + (points - center) * scale).astype(np.float32).reshape((-1, 1, 2))


def package_activity_signals(enabled: bool, conveyor_package_count: int, carrying: bool,
                             hand_signal: bool, toward_package: bool) -> dict[str, bool]:
    belt_has_packages = conveyor_package_count > 0
    working = carrying or (belt_has_packages and (toward_package or hand_signal))
    return {
        "candidate_available_no_belt_demand": bool(enabled and not belt_has_packages and not carrying),
        "candidate_working_with_package": bool(enabled and working),
        "candidate_belt_bottleneck": bool(enabled and belt_has_packages and not carrying and not toward_package and not hand_signal),
        "candidate_waiting_full_belt": bool(enabled and conveyor_package_count >= 4 and not hand_signal and not carrying),
    }


def staffing_pressure(package_count: int, active_worker_count: int) -> bool:
    return package_count >= 3 and active_worker_count >= 2


