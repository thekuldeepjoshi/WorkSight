---
name: ws-ops
description: Query WorkSight's local SQLite video analysis to answer operational questions about worker presence, possible conveyor interactions, activity gaps, and staffing signals. Use when a question depends on processed WorkSight video data or needs supporting video timestamps.
---

# WorkSight operations analyst

Use this skill to answer operations questions from processed WorkSight data in the current project. Prefer the database's frame-level signals to event CSV summaries when a question asks about simultaneous workers, busy periods, or staffing patterns.

## Query the processed data

1. Locate `data/worksight.sqlite3`. If it is unavailable, say what data is missing; do not invent results.
2. List videos, select the relevant completed analysis, and pass its ID to every subsequent query. Each new processing run gets a separate analysis/video ID; `--run-id` is an alias for `--video-id`. Never combine runs:

   ```bash
   python3 scripts/query_worksight.py --db data/worksight.sqlite3 videos
   python3 scripts/query_worksight.py --db data/worksight.sqlite3 summary --video-id VIDEO_ID
   python3 scripts/query_worksight.py --db data/worksight.sqlite3 timeline --video-id VIDEO_ID --window 60 --order busy
   python3 scripts/query_worksight.py --db data/worksight.sqlite3 timeline --video-id VIDEO_ID --window 60 --order quiet
   python3 scripts/query_worksight.py --db data/worksight.sqlite3 imbalances --video-id VIDEO_ID --window 30 --min-present 3 --max-active 1
   python3 scripts/query_worksight.py --db data/worksight.sqlite3 events --video-id VIDEO_ID --event-type possible_belt_interaction
   python3 scripts/query_worksight.py --db data/worksight.sqlite3 events --video-id VIDEO_ID --event-type possible_hand_belt_proximity
   python3 scripts/query_worksight.py --db data/worksight.sqlite3 events --video-id VIDEO_ID --event-type possible_person_in_conveyor_zone
   ```

   The helper is read-only and returns JSON. Use `--help` for all options. Events include `candidate_available_no_belt_demand`, `candidate_working_with_package`, `candidate_belt_bottleneck`, `candidate_waiting_full_belt`, `candidate_staffing_pressure`, `possible_throwing_risk`, and `possible_body_on_conveyor` in addition to the original pose/zone events. For a different question, run a focused read-only SQL query and filter to the selected video ID.

3. Use event intervals or frame-level rows to select specific supporting timestamps. Check that `videos.output_path` exists and refer to the annotated video. Inspect the relevant clip if making a visual claim; do not imply that a segment was reviewed if it was not.

## Use accurate operational language

- `wrist_on_belt=1` or `possible_belt_interaction` means a visible wrist keypoint was inside the conveyor polygon. `possible_hand_belt_proximity` means a detected fingertip landmark was inside it. Call these **candidate belt activity** or **possible proximity**, not confirmed contact or unloading.
- `possible_person_in_conveyor_zone` means an ankle landmark (or fallback footpoint) entered the conveyor polygon. Treat it as a safety-review cue, not a confirmed hazard.
- `possible_body_on_conveyor` means at least two visible hip/knee/ankle pose points overlapped the belt polygon. Treat this as a high-priority major-risk review cue; it is not a verified sit/stand classification.
- `candidate_available_no_belt_demand` means no package was detected in the belt polygon and no package was estimated by wrist proximity. A package-in-hand candidate blocks this availability label.
- `candidate_working_with_package` can be raised by a wrist/package proximity, belt-zone hand signal, or movement toward a tracked belt package. This is not proof of a completed unload.
- `candidate_belt_bottleneck` / `candidate_waiting_full_belt` mean packages were detected without a carry/approach signal; “full” currently means four package tracks.
- `candidate_staffing_pressure` means three or more tracked boxes and at least two candidate-active workers. Treat this as demand evidence to inspect over time, not a direct staffing recommendation.
- `possible_throwing_risk` means a package track moved rapidly near an estimated wrist. It is a motion heuristic and must be checked in the video.
- `in_work_area=1` means the tracked person's footpoint was inside the configured work-area polygon. This is **presence**, not proof they were working or idle.
- To find “three present but one showing candidate activity,” compare distinct track IDs **within the same frame**: `in_work_area=1` versus `wrist_on_belt=1` or a matching `hand_detections.fingertips_in_conveyor=1` row. Aggregate qualifying frames into short windows and report both the frame count and share of tracked-person observation frames. Frames without a tracked person are excluded from that denominator.
- Package detections use YOLO-World on every sampled analysis frame and can miss or mislabel objects. Summary includes nonnegative unique package track IDs separately from repeated frame observations. Tracks are not verified parcels or completed unloads. Belt counts are not calibrated occupancy; review representative clips.
- A fresh database after explicit reset contains no analyses until a new video is processed. Dashboard CSV imports do not populate raw frame evidence in SQLite.
- Completed throughput, calibrated belt occupancy/capacity, task quality, and required staffing are not directly measured. Treat the new candidate events as review signals, not verified operations facts.
- IDs are anonymous and temporary within a video. `events.confidence` is person detector confidence, not confidence that an activity interpretation is correct.
- Consider zone placement, duration thresholds, missed detections, occlusion, frame coverage, and tracker ID changes when interpreting signals.

## Report findings with evidence

Lead with the result and time range. State the metric definition and denominator, then include supporting event IDs or frame counts, exact timestamps, video name, and a link to the local annotated video path when available. For a staffing-mismatch claim, provide representative moments within the window and phrase the result as a review candidate, not proof of idleness. Present efficiency improvements as hypotheses supported by repeated timestamps; do not recommend staffing changes from this signal alone.
