"""One persistent job at a time; heavy CV runs in an isolated subprocess."""
import json
import os
import signal
import subprocess
import sys
import threading
from datetime import datetime, timezone
from uuid import uuid4

from worksight.config import DB_PATH, DATA_DIR, ROOT
from worksight.storage import connect_db, ensure_video, get_video, get_zones, save_zone


def now():
    return datetime.now(timezone.utc).isoformat()


class Jobs:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None

    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

    def recover(self):
        # Called once by the single local server at startup; interrupted runs can be rerun.
        with connect_db() as db:
            db.execute("UPDATE runs SET status='failed', error='Server restarted; start a new run.', updated_at=? WHERE status IN ('queued','processing')", (now(),))
            db.execute("UPDATE videos SET status='failed', error='Processing interrupted by server restart' WHERE status='processing'")

    def start(self, source_id, fps):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise ValueError("A video is already processing. Wait for it to finish.")
            video = get_video(source_id)
            if not video:
                raise LookupError("Unknown video ID")
            zones = get_zones(source_id)
            if not {"conveyor", "work_area"}.issubset(zones):
                raise ValueError("Save both zones before processing.")
            run_id = uuid4().hex
            ensure_video(run_id, video["source_name"], video["source_path"])
            for name, points in zones.items():
                save_zone(run_id, name, points)
            with connect_db() as db:
                db.execute("INSERT INTO runs VALUES (?, ?, 'queued', 0, NULL, ?, ?)", (run_id, source_id, now(), now()))
            log_dir = DATA_DIR / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            env = {**os.environ, "WORKSIGHT_DB": str(DB_PATH), "WORKSIGHT_DATA_DIR": str(DATA_DIR)}
            try:
                with (log_dir / f"{run_id}.log").open("w") as log:
                    self.process = subprocess.Popen([sys.executable, "-m", "backend.worker", run_id, str(fps)], cwd=ROOT, env=env, stdout=log, stderr=log)
            except Exception as error:
                with connect_db() as db:
                    db.execute("UPDATE runs SET status='failed',error=?,updated_at=? WHERE id=?", (str(error), now(), run_id))
                raise
            threading.Thread(target=self._watch, args=(self.process, run_id), daemon=True).start()
            return run_id

    def _watch(self, process, run_id):
        code = process.wait()
        if code:
            with connect_db() as db:
                db.execute("UPDATE runs SET status='failed', error=COALESCE(error, ?),updated_at=? WHERE id=? AND status IN ('queued','processing','paused')", (f"Worker exited ({code}). See data/logs/{run_id}.log", now(), run_id))

    def status(self, run_id):
        with connect_db() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise LookupError("Unknown run ID")
        return dict(row)

    def _owned_process(self, run_id):
        process = self.process
        if process is None or process.poll() is not None:
            raise ValueError("No active processing job")
        with connect_db() as db:
            row = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise LookupError("Unknown run ID")
        return process, row["status"]

    def pause(self, run_id):
        with self.lock:
            process, status = self._owned_process(run_id)
            if status != "processing":
                raise ValueError("Only a processing job can be paused")
            process.send_signal(signal.SIGSTOP)
            with connect_db() as db:
                db.execute("UPDATE runs SET status='paused',updated_at=? WHERE id=?", (now(), run_id))

    def resume(self, run_id):
        with self.lock:
            process, status = self._owned_process(run_id)
            if status != "paused":
                raise ValueError("Only a paused job can be resumed")
            process.send_signal(signal.SIGCONT)
            with connect_db() as db:
                db.execute("UPDATE runs SET status='processing',updated_at=? WHERE id=?", (now(), run_id))

    def cancel(self, run_id):
        with self.lock:
            process, status = self._owned_process(run_id)
            if status not in ("queued", "processing", "paused"):
                raise ValueError("This job is no longer active")
            if status == "paused":
                process.send_signal(signal.SIGCONT)
            with connect_db() as db:
                db.execute("UPDATE runs SET status='cancelled',error='Cancelled by user',updated_at=? WHERE id=?", (now(), run_id))
                db.execute("UPDATE videos SET status='cancelled',error='Cancelled by user' WHERE id=?", (run_id,))
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            self.process = None
