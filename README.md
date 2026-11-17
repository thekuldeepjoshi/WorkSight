# WorkSight

WorkSight turns unloading videos into a local operations dashboard. It uses React, Flask, SQLite, YOLO pose, MediaPipe Hands, and YOLO-World package detection. Throughput and calibrated conveyor capacity are not measured yet; safety and slowdown signals are review candidates.

## Requirements

Local setup needs Python 3.12, Node.js 20+, npm, and these model files in the project root:

- `yolo26n-pose.pt`
- `yolov8s-world.pt`

Docker users only need Docker Desktop.

## Local setup

```bash
cd "/Users/kuldeep/Interview prep/WorkSight"
python3.12 -m venv .venv-intel
./.venv-intel/bin/python -m pip install --upgrade pip
./.venv-intel/bin/python -m pip install -r requirements.txt
npm ci --prefix dashboard
./start.sh
```

Open http://127.0.0.1:5001. Upload a video, draw conveyor and work-area polygons, choose FPS, and process it. Active processing can be paused, resumed, or cancelled. Stop the app with `Ctrl+C`. Data is stored under `data/`.

## Docker

```bash
docker compose up --build -d worksight
```

Open http://localhost:5001. Stop with `docker compose down`. The `worksight-data` volume preserves the database, uploads, logs, and processed outputs.

## Claude Desktop MCP

The read-only MCP server is `mcp_server/worksight_server.py`. It exposes analysis lists, summaries, timelines, review events, and staffing queries.

For local MCP:

```bash
./.venv-intel/bin/python -m pip install -r requirements-mcp.txt
```

Add the configuration in `docs/claude-desktop-mcp.md` to `~/Library/Application Support/Claude/claude_desktop_config.json`, then restart Claude Desktop.

For Docker MCP, start the app first:

```bash
docker compose up --build -d worksight
```

Use this Claude Desktop entry, replacing the project path:

```json
{
  "mcpServers": {
    "worksight-ops": {
      "command": "docker",
      "args": ["compose", "-f", "/absolute/path/to/WorkSight/docker-compose.yml", "run", "--rm", "-T", "--no-deps", "worksight-mcp"]
    }
  }
}
```

Fully quit and reopen Claude Desktop. The Docker MCP server reads the same data volume and cannot modify the database.

## Database queries

```bash
./.venv-intel/bin/python scripts/query_worksight.py videos
./.venv-intel/bin/python scripts/query_worksight.py summary --run-id RUN_ID
./.venv-intel/bin/python scripts/query_worksight.py timeline --run-id RUN_ID --window 60 --order busy
./.venv-intel/bin/python scripts/query_worksight.py events --run-id RUN_ID --event-type possible_person_in_conveyor_zone
```

## Tests

```bash
./.venv-intel/bin/python -m unittest discover -s tests -v
npm run build --prefix dashboard
```

## Layout

- `backend/`: Flask API, jobs, and worker process
- `dashboard/`: React + Vite frontend
- `worksight/`: SQLite, model, CSV, and configuration helpers
- `WorkSight.py`: video processing and operational rules
- `mcp_server/`: read-only Claude Desktop MCP server
- `scripts/`: query and model validation utilities
- `tests/`: automated tests
- `data/`: database, uploads, logs, and outputs

## Limitations

Track IDs are anonymous and temporary. Package counts are estimates. Safety and slowdown events are review candidates, not confirmed incidents. Throughput, calibrated belt occupancy, and required staffing are not measured.
