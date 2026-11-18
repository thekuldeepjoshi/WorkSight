"""Read-only MCP server for Claude Desktop and WorkSight's SQLite database."""

from __future__ import annotations

from contextlib import closing
import json
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from scripts import query_worksight as queries
from worksight.config import DB_PATH
from worksight.storage import open_readonly

mcp = FastMCP("worksight-ops")


def query_analysis(query, video_id, **options):
    """Open the local database and run one shared analysis query."""
    with closing(open_readonly(DB_PATH)) as db:
        result = query(db, queries.select_video(db, video_id.strip()), **options)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def list_analyses() -> str:
    """List available WorkSight videos and completed analysis IDs."""
    with closing(open_readonly(DB_PATH)) as db:
        return json.dumps(queries.list_videos(db), ensure_ascii=False)


@mcp.tool()
def analysis_summary(video_id: str = "") -> str:
    """Return counts for workers, hands, packages, events and evidence files."""
    return query_analysis(queries.summary, video_id)


@mcp.tool()
def activity_timeline(video_id: str = "", window_seconds: int = 60, order: str = "busy") -> str:
    """Rank video windows by detected activity. Order can be busy, quiet, or events."""
    return query_analysis(queries.timeline, video_id, window=max(1, window_seconds), order=order)


@mcp.tool()
def review_events(video_id: str = "", event_type: str = "", limit: int = 100) -> str:
    """Return timestamped review events, optionally filtered by event type."""
    return query_analysis(queries.events, video_id, event_type=event_type.strip(), limit=max(1, min(limit, 500)))


@mcp.tool()
def staffing_imbalances(video_id: str = "", window_seconds: int = 30, min_present: int = 3, max_active: int = 1) -> str:
    """Find periods with many workers present and few candidate-active workers."""
    return query_analysis(
        queries.imbalances, video_id, window=max(1, window_seconds),
        min_present=max(1, min_present), max_active=max(0, max_active),
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
