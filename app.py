from __future__ import annotations

import hashlib
import re
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
import streamlit as st
from PIL import Image
from streamlit_drawable_canvas import st_canvas

from WorkSight import (
    ANALYSIS_VERSION,
    DATA_DIR,
    UPLOADS_DIR,
    delete_zone,
    ensure_video,
    get_events,
    get_summary,
    get_video,
    get_zones,
    inspect_video,
    list_videos,
    process_video,
    save_zone,
    zone_signature,
)


st.set_page_config(page_title="WorkSight Operations", page_icon="📹", layout="wide")
st.title("WorkSight Operations Prototype")
st.caption(
    "Local video analysis for package flow, worker activity, bottlenecks, and safety review. "
    "Track IDs are anonymous and only apply within one video."
)


@st.cache_data(show_spinner=False)
def cached_video_frame(path: str, seconds: int, modified_ns: int, size: int):
    del modified_ns, size
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise ValueError(f"Could not open video: {path}")
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
        ok, frame = capture.read()
        if not ok:
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = capture.read()
        if not ok:
            raise ValueError("Could not read a preview frame from this video.")
        return frame
    finally:
        capture.release()


def save_upload(uploaded_file) -> tuple[Path, str]:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    content = uploaded_file.getbuffer()
    digest = hashlib.sha256(content).hexdigest()
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(uploaded_file.name).name)
    destination = UPLOADS_DIR / f"{digest[:12]}_{safe_name}"
    if not destination.exists():
        with destination.open("wb") as output_file:
            output_file.write(content)
    return destination, digest


def preview_with_zones(frame, zones):
    image = frame.copy()
    colors = {"conveyor": (0, 0, 255), "work_area": (255, 180, 0)}
    height, width = image.shape[:2]
    overlay = image.copy()
    for name, normalized_points in zones.items():
        polygon = [
            (int(x * width), int(y * height)) for x, y in normalized_points
        ]
        contour = np.asarray(polygon, dtype=np.int32).reshape((-1, 1, 2))
        cv2.fillPoly(overlay, [contour], colors.get(name, (0, 255, 0)))
        cv2.polylines(image, [contour], True, colors.get(name, (0, 255, 0)), 3)
        cv2.putText(
            image,
            name.replace("_", " ").title(),
            tuple(polygon[0]),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            colors.get(name, (0, 255, 0)),
            2,
            cv2.LINE_AA,
        )
    return cv2.addWeighted(overlay, 0.18, image, 0.82, 0)


def time_label(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


history = list_videos()
source_options = ["Upload a video"]
if history:
    source_options.append("Open saved analysis")

source_mode = st.sidebar.radio("Video source", source_options)
source_path = None
source_name = None
video_id = None

if source_mode == "Upload a video":
    uploaded = st.sidebar.file_uploader(
        "Choose a video",
        type=["mp4", "mov", "avi", "mkv"],
        max_upload_size=2048,
        help="Video files up to 2 GB are supported.",
    )
    if uploaded is not None:
        source_path, video_id = save_upload(uploaded)
        source_name = uploaded.name
elif source_mode == "Open saved analysis":
    saved_by_id = {item["id"]: item for item in history}
    chosen_id = st.sidebar.selectbox(
        "Saved videos",
        list(saved_by_id),
        format_func=lambda item_id: (
            f"{saved_by_id[item_id]['source_name']} · {saved_by_id[item_id]['status']}"
        ),
    )
    saved = saved_by_id[chosen_id]
    source_path = Path(saved["source_path"])
    source_name = saved["source_name"]
    video_id = chosen_id

if source_path is None or video_id is None:
    st.info("Upload a video or open a saved analysis.")
    st.stop()
if not source_path.is_file():
    st.error(f"The video file is no longer available: {source_path}")
    st.stop()

ensure_video(video_id, source_name, source_path)
try:
    video_info = inspect_video(source_path)
except (ValueError, OSError) as error:
    st.error(str(error))
    st.stop()

st.sidebar.caption(
    f"{video_info['width']} × {video_info['height']} · "
    f"{video_info['fps']:.1f} FPS · {video_info['duration_seconds']:.1f} seconds"
)
zones = get_zones(video_id)
setup_tab, analysis_tab, report_tab = st.tabs(
    ["1 · Configure zones", "2 · Process video", "3 · Operations report"]
)

with setup_tab:
    st.subheader("Draw zones on the video")
    st.write(
        "Choose a zone, click at least three corners, then save it. "
        "You can clear and redraw a saved zone."
    )
    source_stat = source_path.stat()
    preview_second = st.slider(
        "Preview position",
        min_value=0,
        max_value=max(1, int(video_info["duration_seconds"])),
        value=min(1, max(1, int(video_info["duration_seconds"]))),
        step=1,
        format="%d sec",
        help="Choose a representative video frame. The saved zone coordinates stay aligned to the full-resolution video.",
    )
    frame = cached_video_frame(
        str(source_path), preview_second, source_stat.st_mtime_ns, source_stat.st_size
    )
    height, width = frame.shape[:2]
    scale = min(900 / width, 600 / height, 1.0)
    canvas_width = max(1, round(width * scale))
    canvas_height = max(1, round(height * scale))
    display_frame = cv2.resize(frame, (canvas_width, canvas_height))
    left, right = st.columns([1.5, 1])
    with left:
        active_zone = st.selectbox(
            "Zone to draw", ["conveyor", "work_area"],
            format_func=lambda name: {
                "conveyor": "Conveyor belt",
                "work_area": "Worker area",
            }[name],
        )
        canvas_revision_key = f"canvas_revision_{video_id}_{active_zone}"
        st.session_state.setdefault(canvas_revision_key, 0)
        canvas = st_canvas(
            background_image=Image.fromarray(
                cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
            ),
            width=canvas_width,
            height=canvas_height,
            drawing_mode="point",
            stroke_color="#ff3b30" if active_zone == "conveyor" else "#007aff",
            point_display_radius=7,
            update_streamlit=True,
            key=(
                f"zone_canvas_{video_id}_{active_zone}_"
                f"{st.session_state[canvas_revision_key]}"
            ),
        )
        point_count = len((canvas.json_data or {}).get("objects", []))
        st.caption(f"{point_count} corner(s) selected")
        save_col, clear_col = st.columns(2)
        with save_col:
            if st.button("Save selected zone", type="primary"):
                objects = (canvas.json_data or {}).get("objects", [])
                if len(objects) < 3:
                    st.warning("Click at least three corners before saving.")
                else:
                    points = [
                        [
                            min(1.0, max(0.0, (float(item["left"]) + float(item.get("radius", 0)) * float(item.get("scaleX", 1))) / canvas_width)),
                            min(1.0, max(0.0, (float(item["top"]) + float(item.get("radius", 0)) * float(item.get("scaleY", 1))) / canvas_height)),
                        ]
                        for item in objects
                        if item.get("type", "").lower() == "circle"
                    ]
                    if len(points) < 3:
                        st.warning("No usable corner points were found. Redraw the zone.")
                    else:
                        save_zone(video_id, active_zone, points)
                        st.rerun()
        with clear_col:
            if st.button("Clear saved zone"):
                delete_zone(video_id, active_zone)
                st.session_state[canvas_revision_key] += 1
                st.rerun()
    with right:
        st.markdown("**Saved zones on selected frame**")
        if zones:
            st.image(
                cv2.cvtColor(preview_with_zones(display_frame, zones), cv2.COLOR_BGR2RGB),
                caption=f"Saved zones over the source frame at {preview_second}s · red: conveyor · blue: work area",
                width="stretch",
            )
        else:
            st.info("Save both zones to enable processing.")
        for name in ("conveyor", "work_area"):
            status = "saved" if name in zones else "not configured"
            st.write(f"{'✅' if name in zones else '⬜'} {name.replace('_', ' ').title()}: {status}")

with analysis_tab:
    st.subheader("Detect packages, workers, and operational cues")
    # MediaPipe 0.10.x requires Protobuf <5, which conflicts with current
    # Streamlit releases. Use YOLO pose wrist keypoints in the supported stack.
    hand_landmarks_available = False
    current_video = get_video(video_id) or {}
    enable_package_detection = st.checkbox(
        "Detect and track packages on the conveyor",
        value=bool(current_video.get("package_detection_enabled", True)),
        help="Uses Ultralytics YOLO-World. Its first run may need to download a CLIP model; if a network certificate blocks it, worker and pose analysis will still complete.",
    )
    if not {"conveyor", "work_area"}.issubset(zones):
        st.warning("Configure and save both zones before processing this video.")
    else:
        signature = zone_signature(zones)
        already_current = (
            current_video.get("status") == "complete"
            and current_video.get("zone_signature") == signature
            and current_video.get("analysis_version") == ANALYSIS_VERSION
            and bool(current_video.get("hand_landmarks_enabled")) == hand_landmarks_available
            and bool(current_video.get("package_detection_enabled")) == enable_package_detection
        )
        if already_current:
            st.success("This video has already been processed with the saved zones.")
            if enable_package_detection and not current_video.get("package_detection_enabled"):
                st.warning(
                    "Worker and pose analysis completed, but package detection could not load its "
                    "YOLO-World/CLIP model (often a certificate download issue). Package-flow metrics "
                    "are unavailable for this analysis."
                )
        elif current_video.get("status") == "complete":
            st.info("The saved analysis predates the current package and operational rules. Reprocess to refresh it.")
        elif current_video.get("status") == "failed" and current_video.get("error"):
            st.error(f"Last processing attempt failed: {current_video['error']}")
        min_interaction = st.slider(
            "Minimum hand/wrist-in-conveyor time to record a candidate (seconds)",
            min_value=0.3,
            max_value=3.0,
            value=0.7,
            step=0.1,
        )
        min_dwell = st.slider(
            "Minimum work-area presence event (seconds)",
            min_value=1.0,
            max_value=10.0,
            value=3.0,
            step=0.5,
        )
        min_conveyor_presence = st.slider(
            "Minimum person-in-conveyor-zone time for a safety review (seconds)",
            min_value=0.2,
            max_value=2.0,
            value=0.5,
            step=0.1,
        )
        st.caption(
            "Uses YOLO26 pose and YOLO-World package detections with ByteTrack. Package-near-wrist, approach, throw, belt fullness, and staffing pressure are review candidates, not confirmed actions."
        )
        if st.button(
            "Process video" if not already_current else "Reprocess with these settings",
            type="primary",
            disabled=not {"conveyor", "work_area"}.issubset(zones),
        ):
            progress_bar = st.progress(0.0)
            progress_text = st.empty()

            def update_progress(done: int, total: int) -> None:
                fraction = min(1.0, done / total) if total else 0.0
                progress_bar.progress(fraction)
                progress_text.text(f"Processed {done:,} of {total:,} frames")

            try:
                output_path = process_video(
                    video_id,
                    source_path,
                    zones,
                    source_name=source_name,
                    minimum_interaction_seconds=min_interaction,
                    minimum_dwell_seconds=min_dwell,
                    minimum_conveyor_presence_seconds=min_conveyor_presence,
                    enable_hand_landmarks=hand_landmarks_available,
                    enable_package_detection=enable_package_detection,
                    progress=update_progress,
                )
                st.success(f"Analysis saved. Annotated video: {output_path}")
                st.rerun()
            except Exception as error:
                st.exception(error)

with report_tab:
    current_video = get_video(video_id) or {}
    if current_video.get("status") != "complete":
        st.info("Process this video to see its operations report.")
    elif current_video.get("zone_signature") != zone_signature(zones):
        st.warning("The zones changed after this analysis. Reprocess the video to refresh the report.")
    else:
        summary = get_summary(video_id)
        output_path = Path(current_video.get("output_path") or "")
        if output_path.is_file():
            st.download_button(
                "Download annotated video",
                output_path.read_bytes(),
                file_name=f"{source_path.stem}_annotated.mp4",
                mime="video/mp4",
            )
        metrics = st.columns(3)
        metrics[0].metric("Tracked people", summary["tracked_people"])
        metrics[1].metric("Frame observations", f"{summary['observations']:,}")
        metrics[2].metric("Wrist-zone candidates", summary["belt_interactions"])
        metrics = st.columns(3)
        metrics[0].metric("Hand-to-belt proximity", summary["hand_belt_proximity_events"])
        metrics[1].metric("People in conveyor zone", summary["person_in_conveyor_zone_events"])
        metrics[2].metric(
            "Work-area presence", f"{summary['work_area_seconds'] / 60:.1f} track-min"
        )
        package_metrics = st.columns(2)
        package_metrics[0].metric("Package observations", f"{summary['package_observations']:,}")
        package_metrics[1].metric("Package observations in belt zone", f"{summary['conveyor_package_observations']:,}")
        st.caption(
            "Track-min may overlap. Package counts are approximate; four boxes is the current full-belt proxy. Throwing and package-in-hand are motion/proximity heuristics. Verify every alert in video."
        )

        events = get_events(video_id)
        belt_events = [
            event for event in events
            if event["event_type"] in {
                "possible_belt_interaction",
                "possible_hand_belt_proximity",
            }
        ]
        safety_events = [
            event for event in events
            if event["event_type"] == "possible_person_in_conveyor_zone"
        ]
        if safety_events:
            st.subheader("Potential conveyor-zone safety reviews")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Time": time_label(event["start_ms"]),
                            "Track": event["track_id"],
                            "Duration (s)": round(event["duration_ms"] / 1000, 1),
                            "Review cue": event["details"]["rule"],
                        }
                        for event in safety_events
                    ]
                ),
                hide_index=True,
            )
        if belt_events:
            counts_by_minute: dict[int, int] = {}
            for event in belt_events:
                minute = event["start_ms"] // 60_000
                counts_by_minute[minute] = counts_by_minute.get(minute, 0) + 1
            chart = pd.DataFrame(
                {
                    "Minute": [f"{minute:02d}:00" for minute in sorted(counts_by_minute)],
                    "Possible belt interactions": [
                        counts_by_minute[minute] for minute in sorted(counts_by_minute)
                    ],
                }
            ).set_index("Minute")
            st.subheader("Potential belt interactions over time")
            st.bar_chart(chart)

        if events:
            event_rows = [
                {
                    "Time": time_label(event["start_ms"]),
                    "Track": event["track_id"],
                    "Signal": event["event_type"].replace("_", " "),
                    "Duration (s)": round(event["duration_ms"] / 1000, 1),
                    "Confidence": round(event["confidence"], 2),
                    "Evidence rule": event["details"].get("rule", ""),
                    "Interpretation": event["details"].get("interpretation", ""),
                }
                for event in events
            ]
            st.subheader("Reviewable events")
            st.dataframe(
                pd.DataFrame(event_rows),
                hide_index=True,
            )
            selected_event_id = st.selectbox(
                "Review an event in the annotated video",
                [event["id"] for event in events],
                format_func=lambda event_id: next(
                    f"{time_label(event['start_ms'])} · "
                    f"{event['event_type'].replace('_', ' ')} · track {event['track_id']}"
                    for event in events
                    if event["id"] == event_id
                ),
            )
            event = next(event for event in events if event["id"] == selected_event_id)
            clip_start = max(0.0, event["start_ms"] / 1000 - 2.0)
            clip_end = min(
                float(current_video.get("duration_seconds") or video_info["duration_seconds"]),
                max(event["end_ms"] / 1000 + 3.0, clip_start + 4.0),
            )
            evidence_path = output_path if output_path.is_file() else source_path
            st.video(
                str(evidence_path),
                start_time=clip_start,
                end_time=clip_end,
            )
        else:
            st.info("No events met the current duration thresholds.")

        if events:
            csv_data = pd.DataFrame(event_rows).to_csv(index=False).encode("utf-8")
            st.download_button(
                "Download events as CSV",
                csv_data,
                file_name=f"{source_path.stem}_events.csv",
                mime="text/csv",
            )
        st.caption(f"Detection, tracking, pose, hand, package, and event observations are stored in {DATA_DIR / 'worksight.sqlite3'}.")
