# Claude Desktop MCP setup

This project includes a read-only MCP server at `mcp_server/worksight_server.py`.
It exposes WorkSight's SQLite analysis through five tools: analysis listing,
summary, activity timeline, review events, and staffing imbalances.

Install the MCP dependency in the project environment:

```bash
./.venv-intel/bin/python -m pip install -r requirements-mcp.txt
```

Add this server to Claude Desktop's configuration file:

`~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "worksight-ops": {
      "command": "/Users/kuldeep/Interview prep/WorkSight/.venv-intel/bin/python",
      "args": ["-m", "mcp_server.worksight_server"],
      "cwd": "/Users/kuldeep/Interview prep/WorkSight",
      "env": {
        "WORKSIGHT_DB": "/Users/kuldeep/Interview prep/WorkSight/data/worksight.sqlite3"
      }
    }
  }
}
```

Restart Claude Desktop. Ask Claude to list the available WorkSight analyses,
then ask questions about the selected completed analysis. The server opens
SQLite in read-only mode through `scripts/query_worksight.py`; it cannot change
the database or process video.
