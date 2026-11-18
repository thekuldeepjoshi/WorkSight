# Job lifecycle controller: starts, monitors, pauses, resumes, and cancels backend.worker subprocesses.

"""One persistent job at a time; heavy CV runs in an isolated subprocess."""
import os
import signal
import subprocess
import sys
import threading
from datetime import datetime, timezone
from uuid import uuid4

from worksight.config import DB_PATH, DATA_DIR, ROOT
from worksight.storage import connect_db, ensure_video, get_video, get_zones, save_zones

ACTIVE_STATUSES = ("queued", "processing", "paused")


def now():
    """Return a UTC timestamp for database status updates."""
    return datetime.now(timezone.utc).isoformat()


def update_run(db, run_id, *, status=None, progress=None, error=None, expected=None):
    """Update job state and mirror terminal failures to the analysis record."""
    result = db.execute(
        """UPDATE runs SET status=COALESCE(?, status), progress=COALESCE(?, progress),
               error=COALESCE(?, error), updated_at=?
           WHERE id=? AND (? IS NULL OR status=?)""",
        (status, progress, error, now(), run_id, expected, expected),
    )
    if result.rowcount and status in ("failed", "cancelled"):
        db.execute("UPDATE videos SET status=?, error=? WHERE id=?", (status, error, run_id))
    return bool(result.rowcount)


class Jobs:
    """Manage the single video-processing subprocess used by the local app."""

    def __init__(self):
        """Keep the active process and run ID safe across API requests."""
        self.lock = threading.Lock()
        self.process = None
        self.run_id = None

    def close(self):
        """Stop the worker when the API server closes."""
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                self._stop_worker(self.process)

    @staticmethod
    def _stop_worker(process):
        """Ask a worker to stop, then force it only if it does not exit."""
        process.terminate()
        process.send_signal(signal.SIGCONT)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def recover(self):
        """Mark jobs interrupted by a server restart as failed."""
        # Called once by the single local server at startup; interrupted runs can be rerun.
        with connect_db() as db:
            interrupted = db.execute("SELECT id FROM runs WHERE status IN (?, ?, ?)", ACTIVE_STATUSES).fetchall()
            for run in interrupted:
                update_run(db, run["id"], status="failed", error="Server restarted. Start a new run.")
            db.execute("UPDATE videos SET status='failed', error='Processing interrupted by server restart' WHERE status='processing'")

    def start(self, source_id, fps):
        """Copy the zones and launch processing for an uploaded video."""
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
            save_zones(run_id, zones)
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
                    update_run(db, run_id, status="failed", error=str(error))
                raise
            self.run_id = run_id
            threading.Thread(target=self._watch, args=(self.process, run_id), daemon=True).start()
            return run_id

    def _watch(self, process, run_id):
        """Mark the run as failed if its worker exits with an error."""
        code = process.wait()
        if code:
            with self.lock:
                status = self.status(run_id)["status"]
                if status in ACTIVE_STATUSES:
                    with connect_db() as db:
                        update_run(db, run_id, status="failed", expected=status,
                                   error=f"Worker exited ({code}). See data/logs/{run_id}.log")

    def status(self, run_id):
        """Read the latest run status from SQLite."""
        with connect_db() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise LookupError("Unknown run ID")
        return dict(row)

    def _owned_process(self, run_id):
        """Return the active process only when it belongs to this run."""
        process = self.process
        if process is None or run_id != self.run_id or process.poll() is not None:
            raise ValueError("No active processing job")
        return process, self.status(run_id)["status"]

    def pause(self, run_id):
        """Pause a running worker without losing its current frame."""
        with self.lock:
            process, status = self._owned_process(run_id)
            if status != "processing":
                raise ValueError("Only a processing job can be paused")
            with connect_db() as db:
                if not update_run(db, run_id, status="paused", expected=status):
                    raise ValueError("This job is no longer processing")
            process.send_signal(signal.SIGSTOP)

    def resume(self, run_id):
        """Continue a worker from its paused frame."""
        with self.lock:
            process, status = self._owned_process(run_id)
            if status != "paused":
                raise ValueError("Only a paused job can be resumed")
            process.send_signal(signal.SIGCONT)
            with connect_db() as db:
                update_run(db, run_id, status="processing", expected=status)

    def cancel(self, run_id):
        """Stop processing and mark the run as cancelled."""
        with self.lock:
            process, status = self._owned_process(run_id)
            if status not in ACTIVE_STATUSES:
                raise ValueError("This job is no longer active")
            self._stop_worker(process)
            status = self.status(run_id)["status"]
            with connect_db() as db:
                if status in ACTIVE_STATUSES:
                    update_run(db, run_id, status="cancelled", error="Cancelled by user", expected=status)
            self.process = None
            self.run_id = None
