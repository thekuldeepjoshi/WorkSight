# WorkSight

WorkSight turns unloading videos into a local operations dashboard. It uses React, Flask, SQLite, YOLO pose, MediaPipe Hands, and YOLO-World package detection. Throughput and calibrated conveyor capacity are not measured yet; safety and slowdown signals are review candidates.

## Application architecture

```text
User uploads video
        |
        v
React dashboard  <---->  Flask API
                              |
                              v
                    Background processing worker
                       |       |        |
                    YOLO    MediaPipe  YOLO-World
                  people/pose  hands    packages
                              |
                              v
                    SQLite + annotated video + CSV
                              |
                              v
                    Dashboard timelines and alerts
                              |
                              v
                    Claude Desktop MCP server
```

The React dashboard is the customer-facing interface. Flask receives uploads,
manages processing jobs, and serves results. The processing worker analyzes the
video with the computer-vision models and saves frame observations, events, and
timestamps in SQLite. The dashboard reads those results without reprocessing
the video. The read-only MCP server lets Claude query the same SQLite data and
return operational answers with supporting timestamps.

### Why this architecture

- **React** keeps the customer experience interactive and makes video evidence,
  timelines, and alerts easy to review.
- **Flask** provides a small local API for uploads, zones, job status, and files
  without requiring a separate cloud service.
- **A background worker** keeps heavy computer-vision processing away from the
  web interface, so the dashboard remains responsive.
- **SQLite** is simple to install, works offline, and preserves frame-level
  evidence without requiring a database server.
- **Separate model modules** make pose, hand, and package detection easier to
  test and replace.
- **MCP** gives Claude read-only access to the same stored evidence instead of
  asking Claude to reprocess videos or rely on unsupported guesses.

## Requirements

Local setup needs Python 3.12, Node.js 20+, npm, and these model files in the project root:

- `yolo26n-pose.pt`
- `yolov8s-world.pt`

Docker users only need Docker Desktop.

## Local installation and first run

Follow these steps on a new machine.

### 1. Download the project

```bash
git clone https://github.com/thekuldeepjoshi/WorkSight.git
cd WorkSight
```

### 2. Check the required tools

```bash
python3.12 --version
node --version
npm --version
```

Install Python 3.12 and Node.js 20+ if any command is missing.

### 3. Create the Python environment and install dependencies

```bash
python3.12 -m venv .venv-intel
./.venv-intel/bin/python -m pip install --upgrade pip
./.venv-intel/bin/python -m pip install -r requirements.txt
npm ci --prefix dashboard
```

### 4. Add the model files

Copy `yolo26n-pose.pt` and `yolov8s-world.pt` into the project root. Without
these files, video processing will stop during model preflight.

### 5. Start the application

```bash
./start.sh
```

Open http://127.0.0.1:5001. Upload a video, draw conveyor and work-area polygons, choose FPS, and process it. Active processing can be paused, resumed, or cancelled. Stop the app with `Ctrl+C`. Data is stored under `data/`.

## Included demo files

The shared ZIP package includes the original video, an annotated video, and its
events CSV. You can use the original video to run a new analysis, or load the
annotated video and CSV directly in the dashboard to review the supplied
analysis. You are also welcome to upload and process another video.

### 6. Run it again later

The dependencies do not need to be installed again. From the project folder,
run only:

```bash
./start.sh
```

### 7. Stop or reset the local process

Press `Ctrl+C` in the terminal running WorkSight. The application does not reset
the database automatically. Existing analyses remain in `data/`.

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
- `worksight/pipeline.py`: video processing, event generation, and evidence export
- `mcp_server/`: read-only Claude Desktop MCP server
- `scripts/`: query and model validation utilities
- `tests/`: automated tests
- `data/`: database, uploads, logs, and outputs

## Limitations

Track IDs are anonymous and temporary. Package counts are estimates. Safety and slowdown events are review candidates, not confirmed incidents. Throughput, calibrated belt occupancy, and required staffing are not measured.
