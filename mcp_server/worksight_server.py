"""Read-only MCP server for Claude Desktop and WorkSight's SQLite database."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("WORKSIGHT_DB", ROOT / "data" / "worksight.sqlite3")).expanduser().resolve()
mcp = FastMCP("worksight-ops")


def query(*args: str) -> str:
    command = [sys.executable, str(ROOT / "scripts" / "query_worksight.py"), "--db", str(DB_PATH), *args]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "WorkSight query failed")
    return result.stdout.strip()


@mcp.tool()
def list_analyses() -> str:
    """List available WorkSight videos and completed analysis IDs."""
    return query("videos")


@mcp.tool()
def analysis_summary(video_id: str = "") -> str:
    """Return counts for workers, hands, packages, events and evidence files."""
    args = ["summary"]
    if video_id.strip():
        args += ["--video-id", video_id.strip()]
    return query(*args)


@mcp.tool()
def activity_timeline(video_id: str = "", window_seconds: int = 60, order: str = "busy") -> str:
    """Rank video windows by detected activity. Order can be busy, quiet, or events."""
    if order not in {"busy", "quiet", "events"}:
        raise ValueError("order must be busy, quiet, or events")
    args = ["timeline", "--window", str(max(1, window_seconds)), "--order", order]
    if video_id.strip():
        args += ["--video-id", video_id.strip()]
    return query(*args)


@mcp.tool()
def review_events(video_id: str = "", event_type: str = "", limit: int = 100) -> str:
    """Return timestamped review events, optionally filtered by event type."""
    args = ["events", "--limit", str(max(1, min(limit, 500)))]
    if video_id.strip():
        args += ["--video-id", video_id.strip()]
    if event_type.strip():
        args += ["--event-type", event_type.strip()]
    return query(*args)


@mcp.tool()
def staffing_imbalances(video_id: str = "", window_seconds: int = 30, min_present: int = 3, max_active: int = 1) -> str:
    """Find periods with many workers present and few candidate-active workers."""
    args = ["imbalances", "--window", str(max(1, window_seconds)), "--min-present", str(max(1, min_present)), "--max-active", str(max(0, max_active))]
    if video_id.strip():
        args += ["--video-id", video_id.strip()]
    return query(*args)


if __name__ == "__main__":
    mcp.run(transport="stdio")
