# WorkSight data schema and interpretation

The database is `data/worksight.sqlite3`. Use SQLite read-only. Its primary tables are:

- `videos`: video ID, source name/path, processing status, duration, and annotated output path.
- `runs`: ID matching a `videos` analysis record, `source_video_id` pointing at the original upload, status, progress, error and timestamps. Each processing run has a separate analysis record; use its ID for evidence queries.
- `zones`: normalized polygon points for `conveyor` and `work_area`.
- `detections`: one person observation per frame; `frame_index`, `timestamp_ms`, temporary `track_id`, person box confidence, bounding box and pose JSON, plus `in_work_area` and `wrist_on_belt` flags.
- `hand_detections`: per-frame MediaPipe hand observations linked to a person track when possible. `landmarks_json` contains 21 normalized hand landmarks; `fingertips_in_conveyor` flags a fingertip inside the configured conveyor polygon; `handedness_confidence` describes left/right classification confidence.
- `events`: contiguous intervals for work-area presence, pose-wrist proximity, fingertip proximity, and person entry into the conveyor zone, with start/end milliseconds, duration, evidence frame, detector confidence, and a JSON explanation.
- `package_detections`: per-frame package boxes, class, confidence, temporary package track ID and conveyor-zone flag. Count distinct nonnegative track IDs for package tracks; row counts are repeated observations. Neither measures throughput.

## What the flags mean

- `in_work_area=1`: the tracked person's bottom-center point fell inside the saved work-area polygon on that frame.
- `wrist_on_belt=1`: at least one visible wrist keypoint (pose confidence >= 0.25) fell inside the conveyor polygon on that frame.
- `hand_detections.fingertips_in_conveyor=1`: at least one detected fingertip landmark fell inside the conveyor polygon on that frame. This is possible proximity, not confirmed physical contact.
- A candidate-active worker is a distinct non-null track ID with `wrist_on_belt=1` or a linked fingertip-in-zone observation on that frame. This is a proxy, not task recognition.
- `possible_person_in_conveyor_zone`: a visible ankle keypoint (or fallback person footpoint when ankles are unavailable) entered the conveyor polygon. Treat this as a review cue, not a confirmed hazard.
- A present worker is a distinct non-null track ID with `in_work_area=1` on that frame. Presence is not proof of active work.
- One frame may include multiple tracks. For staffing comparisons, count tracks per **same frame** first; do not compare distinct people accumulated over a long time window as if they were simultaneously present.

## Example SQL: simultaneous presence and candidate activity

Replace `:video_id`, `:window_ms`, `:min_present`, and `:max_active` with bound parameters. This finds time windows containing frames that meet the stated rule and reports representative timestamps:

```sql
WITH per_frame AS (
  SELECT frame_index, timestamp_ms,
         COUNT(DISTINCT CASE WHEN d.in_work_area = 1 THEN d.track_id END) AS present_count,
         COUNT(DISTINCT CASE WHEN d.wrist_on_belt = 1 OR EXISTS (
           SELECT 1 FROM hand_detections h
           WHERE h.video_id = d.video_id AND h.frame_index = d.frame_index
             AND h.track_id = d.track_id AND h.fingertips_in_conveyor = 1
         ) THEN d.track_id END) AS candidate_active_count
  FROM detections d
  WHERE d.video_id = :video_id AND d.track_id IS NOT NULL
  GROUP BY d.frame_index, d.timestamp_ms
), qualifying AS (
  SELECT *, CAST(timestamp_ms / :window_ms AS INTEGER) AS window_id
  FROM per_frame
  WHERE present_count >= :min_present AND candidate_active_count <= :max_active
)
SELECT window_id, MIN(timestamp_ms) AS first_ms, MAX(timestamp_ms) AS last_ms,
       COUNT(*) AS qualifying_frames,
       ROUND(100.0 * COUNT(*) / NULLIF((
         SELECT COUNT(*) FROM per_frame f WHERE f.timestamp_ms / :window_ms = q.window_id
       ), 0), 1) AS percent_of_observed_frames,
       MIN(present_count) AS min_present, MAX(present_count) AS max_present,
       MIN(candidate_active_count) AS min_candidate_active,
       MAX(candidate_active_count) AS max_candidate_active
FROM qualifying q
GROUP BY window_id
ORDER BY qualifying_frames DESC;
```

The frame share is the fraction of **tracked-person observation frames** in a window that satisfy the rule. Frames without any tracked person are excluded. This is not a share of workers' time or labor utilization. Show at least a few evidence timestamps from within each flagged window.

## Example SQL: event evidence

```sql
SELECT id, track_id, event_type, start_ms, end_ms, duration_ms,
       evidence_frame, confidence, details_json
FROM events
WHERE video_id = :video_id
ORDER BY start_ms;
```

Convert milliseconds to `mm:ss` (or `hh:mm:ss` for long videos). The matching clip is `videos.output_path`; the original is `videos.source_path`. Verify the file exists before linking it. Frame `n` is approximately `n / fps` seconds into the source video. The `events.confidence` value is detector confidence, not event certainty.

## Operational comparison guidance

- **Busiest period:** rank fixed windows by simultaneous candidate-active tracks or average candidate-active tracks derived from per-frame counts. Report the chosen ranking measure and window duration. Event count is a different measure and can favor longer windows.
- **Low detected belt activity:** calculate the share of observations/frames with any wrist or linked fingertip in the conveyor zone, or use intervals between candidate belt events. Call this a proxy; it does not measure belt occupancy or throughput.
- **Potential staffing mismatch:** use same-frame `present_count` and `candidate_active_count`, aggregate qualifying frames by short windows, and inspect representative video timestamps. Do not infer idleness from zone presence alone.
- **Efficiency opportunities:** propose hypotheses (e.g. review recurring long low-signal windows, camera blind spots, work-zone layout) grounded in repeated evidence. Do not recommend changing headcount from this dataset alone.
