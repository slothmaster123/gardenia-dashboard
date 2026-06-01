from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, render_template, request, jsonify, g

from modules.chat import chat_bp
from modules.youtube import youtube_bp, _build_channel_stats
from modules.arrs import arrs_bp
from modules.ideas import ideas_bp
from modules.lightfield import crm_bp
from services.arr_client import get_combined_health
from services.lightfield_client import get_client as get_crm_client

# ── Worker daemon status store (in-memory, thread-safe) ─────────────────────
_worker_lock = threading.Lock()
_worker_status: dict = {
    "running": False,
    "active_workers": 0,
    "last_poll": None,
    "dispatches": 0,
    "tasks_spawned": 0,
    "uptime_seconds": 0,
    "last_error": None,
}

# ── Kanban DB config ─────────────────────────────────────────────────────────
KANBAN_DB_PATH = Path(os.environ.get("HERMES_KANBAN_DB",
                                     os.path.expanduser("~/.hermes/kanban.db")))

STATUS_COLUMNS = ["todo", "ready", "running", "done", "blocked", "archived"]
STATUS_LABELS = {
    "todo": "TODO", "ready": "READY", "running": "RUNNING",
    "done": "DONE", "blocked": "BLOCKED", "archived": "ARCHIVED",
}
STATUS_COLORS = {
    "todo": "#6b6b82", "ready": "#a78bfa", "running": "#34d399",
    "done": "#22c55e", "blocked": "#f59e0b", "archived": "#4b5563",
}


def _get_kanban_db() -> sqlite3.Connection:
    """Return a per-request kanban DB connection (stored on g)."""
    if "kanban_db" not in g:
        g.kanban_db = sqlite3.connect(str(KANBAN_DB_PATH))
        g.kanban_db.row_factory = sqlite3.Row
        g.kanban_db.execute("PRAGMA journal_mode=WAL")
    return g.kanban_db


def _close_kanban_db(exc=None) -> None:
    db = g.pop("kanban_db", None)
    if db is not None:
        db.close()


def create_app() -> Flask:
    app = Flask(__name__)

    # Minimal secret so flash/session doesn't crash. Override in env for production.
    app.secret_key = os.environ.get("GARDENIA_DASHBOARD_SECRET", "dev-secret-change-me")

    # ── Blueprint registration ──────────────────────────────────────────
    app.register_blueprint(chat_bp)
    app.register_blueprint(youtube_bp)
    app.register_blueprint(arrs_bp)
    app.register_blueprint(ideas_bp)
    app.register_blueprint(crm_bp)

    @app.get("/")
    def dashboard_index():
        return render_template("index.html", title="Gardenia Dashboard", active="dashboard")

    @app.get("/tasks")
    def tasks_index():
        return render_template("panel.html", title="Tasks", active="tasks",
                               heading="Kanban Board", note="Live task overview",
                               status_columns=STATUS_COLUMNS,
                               status_labels=STATUS_LABELS,
                               status_colors=STATUS_COLORS)

    @app.get("/api/health")
    def health():
        return {"ok": True, "service": "gardenia-dashboard"}

    # ── Worker daemon heartbeat + status ──────────────────────────────────

    @app.post("/api/worker/heartbeat")
    def worker_heartbeat():
        """Receive heartbeat from worker_daemon.py."""
        data = request.get_json(silent=True) or {}
        with _worker_lock:
            _worker_status["running"] = data.get("running", False)
            _worker_status["active_workers"] = data.get("active_workers", 0)
            _worker_status["last_poll"] = data.get("last_poll")
            _worker_status["dispatches"] = data.get("dispatches", 0)
            _worker_status["tasks_spawned"] = data.get("tasks_spawned", 0)
            _worker_status["uptime_seconds"] = data.get("uptime_seconds", 0)
            _worker_status["last_error"] = data.get("last_error")
        return jsonify({"ok": True, "received_at": datetime.now(timezone.utc).isoformat()})

    @app.get("/api/worker/status")
    def worker_status():
        """Return current worker daemon status."""
        with _worker_lock:
            return jsonify(dict(_worker_status))

    # ── Dashboard stat card partials (HTMX) ───────────────────────────────

    def _is_htmx() -> bool:
        return request.headers.get("HX-Request", "").lower() == "true"

    @app.get("/api/dashboard/stat/youtube")
    def dashboard_stat_youtube():
        """HTMX partial: YouTube stat card — video count + channel count."""
        try:
            channels = _build_channel_stats()
            total_videos = sum(c["video_count"] for c in channels)
            total_channels = len(channels)
        except Exception:
            total_videos = 0
            total_channels = 0

        if _is_htmx():
            return f"""<div class="stat" hx-get="/api/dashboard/stat/youtube" hx-trigger="every 120s" hx-swap="outerHTML">
  <div class="label">YouTube</div>
  <div class="value">{total_videos}</div>
  <div class="hint">{total_channels} channel{'s' if total_channels != 1 else ''} &middot; analyzed</div>
</div>"""
        return jsonify({"total_videos": total_videos, "total_channels": total_channels})

    @app.get("/api/dashboard/stat/media")
    def dashboard_stat_media():
        """HTMX partial: Media health stat card — Sonarr/Radarr/Lidarr status."""
        try:
            health = get_combined_health()
        except Exception:
            health = {}

        up_count = sum(1 for s in health.values() if s.get("reachable"))
        total = len(health) if health else 3

        if up_count == total:
            value_text = "All up"
        elif up_count == 0:
            value_text = "Down"
        else:
            value_text = f"{up_count}/{total}"

        hint_parts = []
        for name in ("sonarr", "radarr", "lidarr"):
            svc = health.get(name, {})
            status_dot = "green" if svc.get("reachable") else "red"
            hint_parts.append(f'<span style="color:var(--{status_dot})">&#9679;</span> {name.title()}')

        if _is_htmx():
            return f"""<div class="stat" hx-get="/api/dashboard/stat/media" hx-trigger="every 60s" hx-swap="outerHTML">
  <div class="label">Media</div>
  <div class="value">{value_text}</div>
  <div class="hint">{" &middot; ".join(hint_parts)}</div>
</div>"""
        return jsonify({"up": up_count, "total": total, "services": health})

    @app.get("/api/dashboard/stat/crm")
    def dashboard_stat_crm():
        """HTMX partial: CRM stat card — total records count."""
        try:
            client = get_crm_client()
            contacts = client.count_contacts()
            accounts = client.count_accounts()
            opps = client.count_opportunities()
            total = contacts + accounts + opps
        except Exception:
            contacts = 0
            accounts = 0
            opps = 0
            total = 0

        if _is_htmx():
            return f"""<div class="stat" hx-get="/api/dashboard/stat/crm" hx-trigger="every 120s" hx-swap="outerHTML">
  <div class="label">CRM</div>
  <div class="value">{total}</div>
  <div class="hint">{contacts} contacts &middot; {accounts} accounts &middot; {opps} opps</div>
</div>"""
        return jsonify({"contacts": contacts, "accounts": accounts, "opportunities": opps, "total": total})

    @app.get("/api/dashboard/stat/tasks")
    def dashboard_stat_tasks():
        """HTMX partial: Tasks stat card — active worker count."""
        with _worker_lock:
            status = dict(_worker_status)

        active = status.get("active_workers", 0)
        running = status.get("running", False)
        dispatches = status.get("dispatches", 0)

        if running:
            status_text = f"{active} active"
        elif active > 0:
            status_text = f"{active} workers"
        else:
            status_text = "Idle"

        if _is_htmx():
            return f"""<div class="stat" hx-get="/api/dashboard/stat/tasks" hx-trigger="every 30s" hx-swap="outerHTML">
  <div class="label">Tasks</div>
  <div class="value">{status_text}</div>
  <div class="hint">{dispatches} dispatched &middot; {'running' if running else 'idle'}</div>
</div>"""
        return jsonify(status)
    # ── Kanban tasks API ────────────────────────────────────────────────────

    @app.get("/api/kanban/tasks")
    def kanban_tasks():
        """Return all non-archived tasks grouped by status, with relations. JSON API."""
        return jsonify(_build_kanban_data())

    @app.get("/api/kanban/board")
    def kanban_board_html():
        """Return the kanban board as an HTML fragment (HTMX)."""
        data = _build_kanban_data()
        return render_template("components/kanban_board.html", **data)

    @app.get("/api/kanban/task/<task_id>")
    def kanban_task_detail(task_id: str):
        """Return full detail for a single task: body, comments, parents, children, runs."""
        return jsonify(_build_task_detail(task_id))

    @app.get("/api/kanban/task/<task_id>/card")
    def kanban_task_card(task_id: str):
        """Return a single task detail card as HTML fragment (for the modal)."""
        task = _build_task_detail(task_id)
        if "error" in task:
            return render_template("components/kanban_detail.html", error=task["error"]), 404
        return render_template("components/kanban_detail.html", task=task)

    # ── Teardown ──────────────────────────────────────────────────────────
    app.teardown_appcontext(_close_kanban_db)

    return app


def _build_kanban_data() -> dict:
    """Build the kanban board data dict (shared by JSON and HTML endpoints)."""
    db = _get_kanban_db()

    # ── Fetch all non-archived tasks ────────────────────────────────
    rows = db.execute("""
        SELECT id, title, body, assignee, status, priority,
               created_at, started_at, completed_at,
               workflow_template_id, current_step_key
        FROM tasks
        WHERE status != 'archived'
        ORDER BY priority DESC, created_at DESC
    """).fetchall()

    # ── Fetch parent task ids and titles for tasks that have parents ──
    task_ids = [r["id"] for r in rows]
    parent_map: dict = {}  # child_id -> [parent dicts]
    if task_ids:
        placeholders = ",".join("?" for _ in task_ids)
        links = db.execute(f"""
            SELECT tl.child_id, t.id, t.title, t.status, t.assignee
            FROM task_links tl
            JOIN tasks t ON t.id = tl.parent_id
            WHERE tl.child_id IN ({placeholders})
        """, task_ids).fetchall()
        for link in links:
            parent_map.setdefault(link["child_id"], []).append({
                "id": link["id"],
                "title": link["title"],
                "status": link["status"],
                "assignee": link["assignee"],
            })

    # ── Fetch latest run for running tasks ────────────────────────────
    run_map: dict = {}
    running_ids = [r["id"] for r in rows if r["status"] == "running"]
    if running_ids:
        for task_id in running_ids:
            run = db.execute("""
                SELECT id, profile, status, started_at, last_heartbeat_at,
                       outcome, summary, error
                FROM task_runs
                WHERE task_id = ?
                ORDER BY id DESC LIMIT 1
            """, (task_id,)).fetchone()
            if run:
                run_map[task_id] = dict(run)

    # ── Group by status ──────────────────────────────────────────────
    groups: dict = {}
    for status in STATUS_COLUMNS:
        groups[status] = []

    for row in rows:
        task = dict(row)
        task["parents"] = parent_map.get(task["id"], [])
        task["current_run"] = run_map.get(task["id"])
        groups.setdefault(task["status"], []).append(task)

    counts = {s: len(groups.get(s, [])) for s in STATUS_COLUMNS}

    return {
        "columns": STATUS_COLUMNS,
        "labels": STATUS_LABELS,
        "colors": STATUS_COLORS,
        "groups": groups,
        "counts": counts,
        "total": len(rows),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _build_task_detail(task_id: str) -> dict:
    """Build a single task detail dict."""
    db = _get_kanban_db()

    task = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        return {"error": "task not found"}

    task_dict = dict(task)

    parents = db.execute("""
        SELECT t.id, t.title, t.status, t.assignee
        FROM task_links tl JOIN tasks t ON t.id = tl.parent_id
        WHERE tl.child_id = ?
    """, (task_id,)).fetchall()
    task_dict["parents"] = [dict(p) for p in parents]

    children = db.execute("""
        SELECT t.id, t.title, t.status, t.assignee
        FROM task_links tl JOIN tasks t ON t.id = tl.child_id
        WHERE tl.parent_id = ?
    """, (task_id,)).fetchall()
    task_dict["children"] = [dict(c) for c in children]

    comments = db.execute("""
        SELECT id, author, body, created_at
        FROM task_comments WHERE task_id = ?
        ORDER BY id ASC
    """, (task_id,)).fetchall()
    task_dict["comments"] = [dict(c) for c in comments]

    runs = db.execute("""
        SELECT id, profile, status, started_at, ended_at,
               outcome, summary, error
        FROM task_runs WHERE task_id = ?
        ORDER BY id DESC LIMIT 10
    """, (task_id,)).fetchall()
    task_dict["runs"] = [dict(r) for r in runs]

    return task_dict


if __name__ == "__main__":
    port = int(os.environ.get("GARDENIA_DASHBOARD_PORT", "8091"))
    app = create_app()
    app.run(host="0.0.0.0", port=port, debug=True)
