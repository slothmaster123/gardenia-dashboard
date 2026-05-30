# Gardenia Unified Dashboard — Architecture

> **Author:** Marcus (Brand Director, architecture design)  
> **Date:** 2026-05-25  
> **Status:** Design Phase — ready for implementation  
> **Target:** Single control panel for YouTube analysis, *arr media stack, agent chat, and kanban task workers.

---

## 1. Overview

The Gardenia Unified Dashboard consolidates four previously siloed systems into a single Flask application running on port **8091**. It inherits the "Gardenia Intelligence" dark-theme design language from the existing YTS web UI (port 8090) and extends it with three new integration modules: *arr media management, Hermes agent chat, and kanban task monitoring with an autonomous worker daemon.

### 1.1 Systems Being Unified

| System | Current State | Integration Strategy |
|---|---|---|
| **YTS** (YouTube Summarizer) | Flask on :8090, reads `/tmp/yts/data/` | Merge directly — data access shared, routes become a Blueprint |
| **AI Chief of Staff** | aiohttp on :8001 (not running) | Not merged (aiohttp incompatible with Flask). Best features (task execution, stats) folded into new chat/tasks modules |
| **Sonarr/Radarr/Lidarr** | Docker containers :8989/:7878/:8686 | REST API proxy through `arr_client.py` service layer |
| **Hermes Kanban + Gateway** | Local process, `~/.hermes/kanban.db` | SQLite read for task views; WebSocket for agent chat |

### 1.2 Key Design Decisions

1. **Flask (not aiohttp).** The YTS codebase is Flask — the team's production-proven stack. No reason to split frameworks. AI Chief of Staff's aiohttp dashboard is archived; its ideas are folded into the new chat/task modules.

2. **Jinja2 + HTMX (not React/Vue SPA).** The YTS dashboard uses server-rendered templates with minimal JS. HTMX adds live updates (polling, partial swaps) without the complexity of a full SPA framework. This keeps the codebase small and maintainable by a solo operator.

3. **Blueprint modules.** Each integration (YouTube, *arr, Chat, Tasks) is an isolated Flask Blueprint with its own routes, templates, and static assets. The main `app.py` only registers blueprints and sets config.

4. **Service layer separation.** API clients (`arr_client.py`, `kanban_client.py`, `hermes_client.py`, `yts_reader.py`) are pure Python modules under `services/` — no Flask dependencies. This means they can be tested independently and reused by the worker daemon.

5. **Port 8091.** YTS remains on 8090 during migration. Once dashboard is stable, YTS port can be redirected or shut down. No conflict.

6. **HTMX for live updates.** Pages use `hx-get` with `hx-trigger="every 30s"` for auto-refreshing data. No WebSocket complexity needed for most panels — polling is simpler and sufficient for local media/task data.

---

## 2. Directory Structure

```
/home/hackbot/Projects/gardenia-dashboard/
│
├── app.py                          # Flask app factory, blueprint registration, config
├── config.py                       # Environment vars, API keys, port numbers, URLs
├── requirements.txt                # Flask, requests, httpx, python-dotenv, markdown
│
├── modules/                        # Flask Blueprints (one per integration)
│   ├── __init__.py
│   ├── youtube.py                  # /youtube/* routes → reads YTS data
│   ├── arrs.py                     # /media/* routes → proxies *arr APIs
│   ├── chat.py                     # /chat/* routes → Hermes agent interface
│   ├── tasks.py                    # /tasks/* routes → kanban board viewer
│   └── health.py                   # /api/health → integration status checks
│
├── services/                       # Pure-Python data/API clients (no Flask deps)
│   ├── __init__.py
│   ├── yts_reader.py               # Read /tmp/yts/data/state.json + analysis/*.md
│   ├── arr_client.py               # HTTP client for Sonarr/Radarr/Lidarr REST APIs
│   ├── kanban_client.py            # SQLite reader for ~/.hermes/kanban.db
│   └── hermes_client.py            # Hermes Gateway WebSocket/REST client
│
├── templates/                      # Jinja2 templates
│   ├── base.html                   # Root layout: nav, CSS vars, dark theme
│   ├── index.html                  # Dashboard overview with stat cards
│   ├── youtube.html                # YouTube analysis panel
│   ├── media.html                  # *arr media management panel
│   ├── chat.html                   # Agent chat interface
│   ├── tasks.html                  # Kanban task board view
│   └── components/                 # Reusable HTMX partials
│       ├── stat_card.html          # Single stat widget
│       ├── video_card.html         # YouTube video summary card
│       ├── media_card.html         # Movie/episode/album card
│       └── task_row.html           # Single kanban task row
│
├── static/
│   ├── style.css                   # CSS (adapted from YTS base.html dark theme)
│   ├── chat.js                     # WebSocket chat client (~80 lines)
│   ├── dashboard.js                # HTMX helpers, toast notifications
│   └── favicon.svg                 # Brand icon
│
├── worker_daemon.py                # Autonomous kanban task poller/executor
│
├── systemd/                        # systemd unit files
│   ├── gardenia-dashboard.service  # Keeps Flask app running on boot
│   └── hermes-worker-poller.service # Keeps worker daemon running
│
├── ARCHITECTURE.md                 # This document
└── DEPLOY.md                       # Setup instructions (created in Task 7)
```

---

## 3. Module: YouTube (`modules/youtube.py`)

Reads directly from the YTS data directory — no API calls needed since it's on the same filesystem.

### 3.1 Data Sources

| Source | Path | Content |
|---|---|---|
| State file | `/tmp/yts/data/state.json` | Channel list, sync history, video metadata |
| Analysis files | `/tmp/yts/data/analysis/*.md` | Markdown analysis per video with VIDEO METADATA block |
| Channels file | `/tmp/yts/channels.json` | Channel RSS config |

### 3.2 Service: `services/yts_reader.py`

```python
# Key functions
get_recent_videos(limit=20) -> list[VideoSummary]
get_video_analysis(video_id: str) -> Analysis | None
get_channels() -> list[Channel]
search_analyses(query: str) -> list[VideoSummary]
get_stats() -> dict  # {channel_count, analysis_count, last_sync}
```

Parses the metadata block from each `.md` file to extract title, channel, published date. Falls back gracefully if files are missing.

### 3.3 API Endpoints

| Method | Route | Returns |
|---|---|---|
| GET | `/api/youtube/videos` | `[{video_id, title, channel, summary_excerpt, date}]` |
| GET | `/api/youtube/videos/<id>` | Full markdown analysis rendered as HTML |
| GET | `/api/youtube/channels` | `[{name, category, video_count}]` |
| GET | `/api/youtube/search?q=` | Filtered videos matching query |
| GET | `/api/youtube/stats` | `{channel_count, analysis_count, last_sync}` |

### 3.4 Frontend (`templates/youtube.html`)

- Video cards in a grid or list (matches YTS `analysis_list.html` style)
- Click expands full analysis inline via HTMX (`hx-get` → loads analysis_detail partial)
- Search bar at top filters client-side or via `/api/youtube/search`
- Channel stat cards at top

---

## 4. Module: *arr Media (`modules/arrs.py`)

Proxies REST API calls to the three Docker *arr containers. Each service requires an API key — stored in `config.py` (loaded from `.env`).

### 4.1 Service: `services/arr_client.py`

```python
class ArrClient:
    def __init__(self, base_url: str, api_key: str):
        self.base = base_url
        self.headers = {"X-Api-Key": api_key}
    
    def get_calendar(self, days=7) -> list[dict]
    def get_wanted(self, page=1) -> list[dict]
    def get_queue(self) -> list[dict]
    def get_system_status() -> dict
    def get_disk_space() -> dict
```

Each *arr service gets its own `ArrClient` instance:
- `sonarr = ArrClient("http://localhost:8989/api/v3", SONARR_API_KEY)`
- `radarr = ArrClient("http://localhost:7878/api/v3", RADARR_API_KEY)`
- `lidarr = ArrClient("http://localhost:8686/api/v1", LIDARR_API_KEY)`

API keys sourced from Docker container environment variables or stored in `.env`.

### 4.2 API Endpoints

| Method | Route | Returns |
|---|---|---|
| GET | `/api/media/sonarr/calendar` | Upcoming episodes (7 days) |
| GET | `/api/media/sonarr/wanted` | Missing/wanted episodes |
| GET | `/api/media/sonarr/queue` | Active downloads |
| GET | `/api/media/radarr/calendar` | Upcoming movies |
| GET | `/api/media/radarr/wanted` | Missing/wanted movies |
| GET | `/api/media/lidarr/wanted` | Missing/wanted albums |
| GET | `/api/media/health` | `{sonarr: "up"|"down", radarr: ..., lidarr: ...}` |
| GET | `/api/media/disk` | Combined disk space across all services |

### 4.3 Frontend (`templates/media.html`)

- Three tabs: TV Shows | Movies | Music
- Each tab shows: upcoming calendar (next 7 days), wanted/missing items, active queue
- Health indicators: green dot (up), red dot (down) next to each service name
- Disk space bar at top

---

## 5. Module: Agent Chat (`modules/chat.py`)

### 5.1 Connection Strategy

Hermes Gateway runs locally. Chat module connects via two paths:

1. **Primary: WebSocket** — `ws://localhost:<gateway_port>/ws` for bidirectional streaming. This gives real-time agent responses.
2. **Fallback: CLI** — `hermes -p <profile> "<message>"` for simple request/response. Useful if WebSocket is unavailable.

### 5.2 Service: `services/hermes_client.py`

```python
class HermesClient:
    def __init__(self, gateway_url: str = "ws://localhost:8765/ws"):
        self.gateway = gateway_url
    
    async def list_profiles() -> list[str]
    async def send_message(profile: str, message: str) -> AsyncIterator[str]
    def send_message_sync(profile: str, message: str) -> str  # CLI fallback
```

Since this is a Flask (sync) app, the chat module uses:
- **CLI fallback** for `/api/chat/send` — spawns `hermes` subprocess, streams stdout
- **WebSocket** from browser-side JavaScript (`static/chat.js`) — direct client-to-gateway WebSocket connection, bypassing Flask entirely

### 5.3 API Endpoints

| Method | Route | Returns |
|---|---|---|
| GET | `/api/chat/profiles` | `["marcus", "lena", "kai", "soren", "nova"]` |
| POST | `/api/chat/send` | `{profile, message}` → agent response text |
| GET | `/api/chat/history` | Recent messages from session DB |

### 5.4 Frontend (`templates/chat.html` + `static/chat.js`)

- Left sidebar: agent profile list (click to select)
- Main area: chat message history, input box
- Message history stored in `localStorage` (per profile)
- WebSocket connection managed by `chat.js` — reconnects on disconnect
- Streaming responses: agent types word-by-word

> **Note:** If WebSocket details (port, path) aren't finalized yet, the chat module defaults to CLI mode via the Flask endpoint, which works immediately.

---

## 6. Module: Kanban Tasks (`modules/tasks.py`)

### 6.1 Service: `services/kanban_client.py`

Reads the shared SQLite kanban database at `~/.hermes/kanban.db`. The kanban dispatcher already writes task state there.

```python
class KanbanClient:
    def __init__(self, db_path: str = "~/.hermes/kanban.db"):
        self.db = Path(db_path).expanduser()
    
    def get_tasks(self, status=None, assignee=None, limit=50) -> list[Task]
    def get_task(self, task_id: str) -> Task | None
    def get_stats(self) -> dict  # {total, todo, ready, running, done, blocked, failed}
```

SQLite schema reference (from Hermes kanban internals):
- `tasks` table: id, title, body, assignee, status, priority, created_at, completed_at
- `events` table: task_id, kind, payload, created_at
- `comments` table: task_id, body, created_at

### 6.2 API Endpoints

| Method | Route | Returns |
|---|---|---|
| GET | `/api/tasks/list` | All non-archived tasks with status |
| GET | `/api/tasks/list?status=ready` | Filtered by status |
| GET | `/api/tasks/list?assignee=marcus` | Filtered by assignee |
| GET | `/api/tasks/<id>` | Task detail with comments and events |
| GET | `/api/tasks/stats` | `{total, todo, ready, running, done, blocked, failed}` |

### 6.3 Frontend (`templates/tasks.html`)

- Kanban-style columns: Ready | Running | Done | Blocked
- Each card: title, assignee, created time
- Click to expand task detail (body, comments, event log)
- Color-coded status badges
- Auto-refresh every 30s via HTMX

---

## 7. Worker Daemon (`worker_daemon.py`)

### 7.1 Purpose

The autonomous worker daemon polls the kanban board for `ready` tasks and spawns Hermes agent processes to claim and execute them. This enables unattended task processing — workers don't wait for human prompting.

### 7.2 Operation Loop

```
Every 60 seconds:
  1. Poll hermes kanban ls --json
  2. For each ready task:
     a. Check if we have capacity (max_concurrent_workers = 3)
     b. Spawn: hermes -p <assignee> kanban claim <task_id>
     c. Track PID, report started
  3. Check on running workers:
     a. If exited, report completion/failure to dashboard API
     b. If running > max_runtime, kill and report timeout
  4. POST status to /api/worker/heartbeat
  5. Sleep 60s
```

### 7.3 Configuration

```python
# worker_daemon.py constants (override via env vars)
POLL_INTERVAL = 60          # seconds
MAX_CONCURRENT = 3          # max parallel workers
MAX_RUNTIME = 3600          # 1 hour max per task
LOG_PATH = "~/.hermes/logs/worker-daemon.log"
DASHBOARD_URL = "http://localhost:8091"
```

### 7.4 systemd Unit

```ini
# /etc/systemd/system/hermes-worker-poller.service
[Unit]
Description=Hermes Kanban Worker Poller
After=network.target

[Service]
Type=simple
User=hackbot
ExecStart=/usr/bin/python3 /home/hackbot/Projects/gardenia-dashboard/worker_daemon.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 7.5 API Endpoints (exposed by worker daemon or dashboard)

| Method | Route | Returns |
|---|---|---|
| GET | `/api/worker/status` | `{running: bool, active_workers: 2, last_poll: "..."}` |
| POST | `/api/worker/trigger` | Manually trigger a poll cycle |
| POST | `/api/worker/heartbeat` | Worker daemon posts its status here |

---

## 8. Main Application (`app.py`)

### 8.1 App Factory

```python
# app.py (pseudocode)
from flask import Flask
from modules.youtube import youtube_bp
from modules.arrs import arrs_bp
from modules.chat import chat_bp
from modules.tasks import tasks_bp
from modules.health import health_bp

def create_app():
    app = Flask(__name__)
    app.config.from_object("config")
    
    app.register_blueprint(youtube_bp, url_prefix="/youtube")
    app.register_blueprint(arrs_bp, url_prefix="/media")
    app.register_blueprint(chat_bp, url_prefix="/chat")
    app.register_blueprint(tasks_bp, url_prefix="/tasks")
    app.register_blueprint(health_bp)  # /api/health, /api/* routes
    
    return app

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=8091, debug=False)
```

### 8.2 Navigation Structure

```
┌──────────────────────────────────────────────────────┐
│  🧠 Gardenia Intelligence           [Dashboard] [YouTube] [Media] [Chat] [Tasks] │
├──────────────────────────────────────────────────────┤
│                                                      │
│   (Active panel content area — HTMX partial swaps)   │
│                                                      │
└──────────────────────────────────────────────────────┘
```

- **Dashboard** (`/`): Overview with stat cards from all integrations
- **YouTube** (`/youtube`): Video analyses, channels, search
- **Media** (`/media`): *arr tabs (TV/Movies/Music)
- **Chat** (`/chat`): Agent chat with profile selector
- **Tasks** (`/tasks`): Kanban board with worker status

### 8.3 systemd Unit

```ini
# /etc/systemd/system/gardenia-dashboard.service
[Unit]
Description=Gardenia Unified Dashboard
After=network.target docker.service

[Service]
Type=simple
User=hackbot
WorkingDirectory=/home/hackbot/Projects/gardenia-dashboard
ExecStart=/usr/bin/python3 app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## 9. Authentication & Security

### 9.1 Current State

All services run on localhost or the local network (`10.0.0.186`). The home firewall blocks external access.

### 9.2 Recommendation

**Phase 1 (MVP):** No auth. Dashboard is local-network only. `0.0.0.0` binding serves LAN but not internet (firewall blocks port forwarding).

**Phase 2 (future):** Simple shared API key in `.env`:
```
DASHBOARD_API_KEY=sk-xxxx
```
Checked via `@app.before_request` for all `/api/*` routes. Frontend pages remain open (read-only views).

---

## 10. HTMX Integration Pattern

Instead of a heavy SPA, the dashboard uses HTMX for live updates:

```html
<!-- Auto-refreshing component: polls every 30s -->
<div hx-get="/api/media/health" hx-trigger="every 30s" hx-swap="innerHTML">
  <!-- Server renders health dots here -->
</div>

<!-- Lazy-loaded panel: loads on click -->
<button hx-get="/youtube/videos" hx-target="#content-area" hx-swap="innerHTML">
  YouTube
</button>
```

**HTMX CDN:** Loaded from `unpkg.com` in `base.html` `<head>`:
```html
<script src="https://unpkg.com/htmx.org@1.9.10"></script>
```

---

## 11. CSS Design System

Inherited from YTS `base.html`. Key design tokens:

```css
:root {
  --bg: #08080c;          /* Deepest background */
  --surface: #0f0f16;     /* Cards, nav */
  --card: #16161f;        /* Interactive cards */
  --border: #252535;      /* Subtle borders */
  --text: #e4e4ee;        /* Primary text */
  --text-secondary: #a0a0b8;
  --muted: #6b6b82;
  --accent: #a78bfa;      /* Purple primary */
  --gold: #f59e0b;        /* Gold accent */
  --green: #34d399;       /* Success/health */
  --red: #f87171;         /* Error/down */
  --font: "Inter", system-ui;
  --font-display: "Playfair Display", serif;  /* Headings */
  --font-mono: "JetBrains Mono";              /* Code */
}
```

---

## 12. Data Flow Diagrams

### 12.1 YouTube Panel

```
Browser ──GET /youtube──▶ Flask (youtube_bp)
                              │
                              ▼
                     services/yts_reader.py
                              │
                              ▼
                     /tmp/yts/data/state.json
                     /tmp/yts/data/analysis/*.md
                              │
                              ▼
                     Rendered Jinja2 template
                              │
                              ▼
Browser ◀──────── HTML with video cards
```

### 12.2 Media Panel

```
Browser ──GET /media/sonarr/calendar──▶ Flask (arrs_bp)
                                            │
                                            ▼
                                   services/arr_client.py
                                            │
                                   HTTP GET (X-Api-Key header)
                                            │
                                            ▼
                                   Docker: sonarr:8989/api/v3/calendar
                                            │
                                            ▼
                                   JSON → Jinja2 template
                                            │
                                            ▼
Browser ◀────────── HTML with episode cards
```

### 12.3 Chat Panel

```
Browser ──WebSocket──▶ Hermes Gateway (:8765/ws)
                            │
                            ▼
                     Agent processes message
                            │
                            ▼
Browser ◀──stream tokens── JSON messages (type, content, done)

(Fallback: Browser ──POST /api/chat/send──▶ Flask ──subprocess──▶ hermes CLI ──▶ response)
```

### 12.4 Task Panel + Worker

```
Worker Daemon (systemd)
    │
    │ every 60s
    ▼
hermes kanban ls --json ──▶ reads ~/.hermes/kanban.db
    │
    │ finds ready tasks
    ▼
spawn: hermes -p <profile> kanban claim <t_id>
    │
    ▼
Worker process runs task autonomously
    │
    ▼
POST /api/worker/heartbeat ──▶ Dashboard updates task status
```

---

## 13. Dependencies (`requirements.txt`)

```
flask>=3.0
requests>=2.31
python-dotenv>=1.0
markdown>=3.5
httpx>=0.27          # For async *arr calls if needed later
```

No additional heavy dependencies. `requests` is already installed. `markdown` is already in YTS venv.

---

## 14. Migration Path from YTS

| Step | Action | Risk |
|---|---|---|
| 1 | Dashboard built on :8091, YTS stays on :8090 | None — parallel operation |
| 2 | YouTube Blueprint reads YTS data directly | Low — read-only, no mutation |
| 3 | All features stable on :8091 | Low — validate for 1 week |
| 4 | YTS :8090 shut down or redirects to :8091 | Low — one-line nginx redirect |

---

## 15. Open Questions & Future Considerations

1. **Gateway WebSocket port:** Needs confirmation — check `hermes gateway` config for actual WS port. Default assumed: 8765.
2. **Authentication for *arr APIs:** API keys live in Docker env. Extract them once for `.env` file.
3. **Mobile responsiveness:** Current YTS CSS uses `max-width: 1200px`. The media panel with tabs may need responsive adjustments.
4. **Notifications:** Future: webhook from dashboard to Telegram/Discord when tasks complete or media downloads finish.
5. **RAG search integration:** YouTube search could query the ChromaDB at `~/.hermes/rag_db` for semantic search beyond keyword matching.

---

## 16. Implementation Sequence (Kanban Tasks)

The plan specifies 7 kanban tasks. Task 1 (this document) is complete. Remaining:

| Task | Title | Assignee | Depends On |
|---|---|---|---|
| t_2 | Build Dashboard Shell | lena | t_1 ✓ |
| t_3 | YouTube Analysis Panel | lena | t_2 |
| t_4 | *arr Media Integration | lena | t_2 |
| t_5 | Agent Chat Interface | lena | t_2 |
| t_6 | Autonomous Worker Daemon | marcus | t_2 |
| t_7 | Integration & Deploy | lena | t_3, t_4, t_5, t_6 |

---

*End of ARCHITECTURE.md*
