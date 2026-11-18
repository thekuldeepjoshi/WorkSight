"""Child process that loads a run, invokes the pipeline, and reports its status."""
import sys
from pathlib import Path

from backend.jobs import update_run
from worksight.storage import connect_db, get_video, get_zones
from worksight.pipeline import process_video


def run(run_id, fps):
    def update(done, total):
        with connect_db() as db:
            update_run(db, run_id, progress=min(0.99, done / max(1, total)))
    try:
        with connect_db() as db:
            update_run(db, run_id, status="processing", expected="queued")
        video = get_video(run_id)
        process_video(run_id, Path(video["source_path"]), get_zones(run_id), source_name=video["source_name"],
                      analysis_fps=fps, progress=update)
        with connect_db() as db:
            update_run(db, run_id, status="complete", progress=1)
    except Exception as error:
        with connect_db() as db:
            update_run(db, run_id, status="failed", error=str(error))
        raise


if __name__ == "__main__":
    run(sys.argv[1], float(sys.argv[2]))
