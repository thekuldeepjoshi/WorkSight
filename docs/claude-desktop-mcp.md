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
      "command": "/absolute/path/to/WorkSight/.venv-intel/bin/python",
      "args": ["/absolute/path/to/WorkSight/mcp_server/worksight_server.py"],
      "env": {
        "WORKSIGHT_DB": "/absolute/path/to/WorkSight/data/worksight.sqlite3"
      }
    }
  }
}
```

Replace `/absolute/path/to/WorkSight` with your project directory and restart
Claude Desktop. Ask Claude to list the available WorkSight analyses and select
a completed run. The MCP server and CLI use the same SQLite queries. MCP opens
the database in read-only mode.
