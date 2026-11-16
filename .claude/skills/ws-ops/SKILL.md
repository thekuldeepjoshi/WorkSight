---
name: ws-ops
description: Query WorkSight's local SQLite video analysis to answer operational questions about worker presence, possible conveyor interactions, activity gaps, and staffing signals. Use this skill when a question depends on processed WorkSight video data or needs supporting video timestamps.
---

# WorkSight operations analyst

Answer operational questions from WorkSight's processed local data. Query the database instead of estimating from the CSV when frame-level counts or pose-derived zone flags matter.

## Start with the data

1. Find `data/worksight.sqlite3` from the project root. If it is missing, report that and ask for the database or an exported events CSV. Do not invent figures.
2. List available videos and select the matching `status='complete'` video. Use the video ID on every query so records from different videos are never combined.
3. Use the bundled read-only helper for common questions:

   ```bash
   python scripts/query_worksight.py --db data/worksight.sqlite3 videos
   python scripts/query_worksight.py --db data/worksight.sqlite3 summary --video-id VIDEO_ID
   python scripts/query_worksight.py --db data/worksight.sqlite3 timeline --video-id VIDEO_ID --window 60
   python scripts/query_worksight.py --db data/worksight.sqlite3 imbalances --video-id VIDEO_ID --window 30 --min-present 3 --max-active 1
   python scripts/query_worksight.py --db data/worksight.sqlite3 events --video-id VIDEO_ID --event-type possible_belt_interaction
   python scripts/query_worksight.py --db data/worksight.sqlite3 events --video-id VIDEO_ID --event-type possible_hand_belt_proximity
   python scripts/query_worksight.py --db data/worksight.sqlite3 events --video-id VIDEO_ID --event-type possible_person_in_conveyor_zone
   ```

   Run `python scripts/query_worksight.py --help` for options. The helper opens SQLite read-only and emits JSON. For a question outside these summaries, write a focused read-only SQL query against the same video ID.
4. Read [the schema and interpretation guide](references/schema-and-analysis.md) before interpreting counts, inactivity, or event confidence.
5. For the strongest claims, use event intervals or frame-level evidence to identify exact timestamps and the annotated video path. Inspect the corresponding video segment when a visual conclusion is important. A database signal alone is not visual confirmation.

## Interpret operational questions carefully

- Treat `wrist_on_belt=1` and `possible_belt_interaction` as **candidate belt activity**. `possible_hand_belt_proximity` means a detected fingertip landmark entered the conveyor zone. Neither confirms contact or unloading.
- Treat `possible_person_in_conveyor_zone` as a safety-review cue based on an ankle landmark or fallback footpoint entering the conveyor polygon, not as a confirmed hazard.
- Treat `in_work_area=1` as **presence in the configured zone**, not engagement or utilization. A frame-level staffing imbalance means several tracks were inside the zone while fewer had a wrist or detected fingertip inside the belt zone at the same frame.
- “Most underutilised” is not directly measured. Rank time windows by the absence or low share of candidate wrist-zone activity, and call them **low detected belt-activity periods**. State the window size and the comparison used.
- Throughput, belt occupancy/capacity, item counts, completed work, waiting, and task quality are not recorded. Do not claim these values or that more/less staff is needed. Describe a possible opportunity as a hypothesis to review.
- Track IDs are temporary and anonymous within one video; do not refer to a track ID as a real person or compare IDs across videos.
- `events.confidence` is the maximum person-box confidence recorded during an event, not the probability that the operational interpretation is true.
- Zone geometry, duration thresholds, occlusion, camera view, and tracker ID switches affect results. Mention these limits when they could change the recommendation.

## Answer with evidence and a practical next step

Lead with the finding, period, and metric. State the operational implication as a qualified hypothesis, then give supporting evidence: video name, timestamp or interval, event IDs / frame counts, denominator, window size, and the exact proxy used. Link to the annotated video using its local path when available. If the path is missing, give the timestamp and say the clip path was unavailable. Never claim to have watched a segment unless you actually inspected it.

For “three present, one working,” report the number and share of **tracked-person observation frames** in each time window where `present_count >= 3` and `candidate_active_count <= 1`; frames without a tracked person are excluded. Also show the window duration and representative timestamps. For “busiest,” define whether ranking by peak simultaneous candidate-active tracks, average candidate-active tracks, or candidate event count, and choose the measure that best answers the question.

Prefer a concise table for ranked periods and a short “what to review next” note. If no complete video or relevant observations exist, explain what is missing rather than substituting a different metric silently.
