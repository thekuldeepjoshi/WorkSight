"""Spawned once for each processing run; no Flask or Streamlit dependency."""
import sys
from pathlib import Path

from backend.jobs import now
from worksight.storage import connect_db, get_video, get_zones
from WorkSight import process_video


def run(run_id, fps):
    def update(done, total):
        with connect_db() as db:
            db.execute("UPDATE runs SET progress=?,updated_at=? WHERE id=?", (min(0.99, done / max(1, total)), now(), run_id))
    try:
        with connect_db() as db:
            db.execute("UPDATE runs SET status='processing',updated_at=? WHERE id=?", (now(), run_id))
        video = get_video(run_id)
        process_video(run_id, Path(video["source_path"]), get_zones(run_id), source_name=video["source_name"],
                      analysis_fps=fps, enable_hand_landmarks=True, enable_package_detection=True, progress=update)
        with connect_db() as db:
            db.execute("UPDATE runs SET status='complete',progress=1,updated_at=? WHERE id=?", (now(), run_id))
    except Exception as error:
        with connect_db() as db:
            db.execute("UPDATE runs SET status='failed',error=?,updated_at=? WHERE id=?", (str(error), now(), run_id))
            db.execute("UPDATE videos SET status='failed',error=? WHERE id=?", (str(error), run_id))
        raise


if __name__ == "__main__":
    run(sys.argv[1], float(sys.argv[2]))
