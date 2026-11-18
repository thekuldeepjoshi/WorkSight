# Central paths and configuration values shared by the backend, worker, and video pipeline.

"""Application paths and analysis-wide constants."""

from pathlib import Path
import os


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("WORKSIGHT_DATA_DIR", ROOT / "data")).expanduser().resolve()
UPLOADS_DIR = DATA_DIR / "uploads"
PROCESSED_DIR = DATA_DIR / "processed"
DB_PATH = Path(os.environ.get("WORKSIGHT_DB", DATA_DIR / "worksight.sqlite3")).expanduser().resolve()

# Record which processing rules produced a run's evidence.
ANALYSIS_VERSION = 9

HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20), (0, 17),
)
HAND_FINGERTIPS = (4, 8, 12, 16, 20)
