#!/usr/bin/env python3
"""
Hermes Kanban Worker Poller Daemon
===================================
Polls the kanban board for ready tasks and spawns worker agents via
`hermes kanban dispatch`. Posts heartbeat to the Gardenia dashboard.

Usage:
  worker_daemon.py                  Run the poller daemon (normal mode)
  worker_daemon.py --dry-run        Run one dispatch tick and exit
  worker_daemon.py --version        Print version and exit
  worker_daemon.py --help           Show this help message

Config (env vars):
  HERMES_POLL_INTERVAL       Seconds between dispatch ticks (default: 60)
  HERMES_MAX_SPAWNS          Max workers spawned per tick (default: 3)
  HERMES_DASHBOARD_URL       Dashboard base URL (default: http://localhost:8091)
  HERMES_WORKER_LOG          Log file path (default: ~/.hermes/logs/worker-daemon.log)
  HERMES_BIN                 Path to hermes CLI (default: hermes from PATH)
  HERMES_HOME                Hermes home directory (default: ~/.hermes)

Signals:
  SIGTERM / SIGINT  -> graceful shutdown (wait for running workers, then exit)
  SIGUSR1           -> trigger immediate poll (bypass sleep)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Version ──

__version__ = "1.0.0"

# ── Configuration ──

POLL_INTERVAL: int = int(os.environ.get("HERMES_POLL_INTERVAL", "60"))
MAX_SPAWNS: int = int(os.environ.get("HERMES_MAX_SPAWNS", "3"))
DASHBOARD_URL: str = os.environ.get("HERMES_DASHBOARD_URL", "http://localhost:8091")
LOG_PATH: Path = Path(
    os.environ.get("HERMES_WORKER_LOG", "~/.hermes/logs/worker-daemon.log")
).expanduser()
HERMES_BIN: str = os.environ.get("HERMES_BIN", "hermes")
HERMES_HOME: str = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))

# ── Logging ──

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("worker-daemon")
logger.setLevel(logging.DEBUG)

fh = logging.FileHandler(str(LOG_PATH))
fh.setLevel(logging.DEBUG)
fh.setFormatter(
    logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
    )
)

ch = logging.StreamHandler(sys.stderr)
ch.setLevel(logging.INFO)
ch.setFormatter(logging.Formatter("[worker-daemon] %(levelname)s %(message)s"))

logger.addHandler(fh)
logger.addHandler(ch)

# ── State ──

running: bool = True
trigger_now: bool = False
start_time: float = time.time()

dispatches: int = 0
tasks_spawned: int = 0
tasks_reclaimed: int = 0
last_poll_time: Optional[str] = None
last_error: Optional[str] = None


# ── Signal handlers ──

def _shutdown(signum: int, _frame) -> None:
    global running
    sig_name = signal.Signals(signum).name
    logger.info("Received %s -- shutting down gracefully", sig_name)
    running = False


def _trigger_now(signum: int, _frame) -> None:
    global trigger_now
    logger.info("Received SIGUSR1 -- triggering immediate poll")
    trigger_now = True


signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGUSR1, _trigger_now)


# ── Dispatch ──

def run_dispatch() -> dict:
    """Run one dispatch tick and return parsed JSON result."""
    global dispatches, tasks_spawned, tasks_reclaimed, last_error

    cmd = [
        HERMES_BIN, "kanban", "dispatch",
        "--max", str(MAX_SPAWNS),
        "--json",
    ]
    logger.debug("Running: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "HERMES_HOME": HERMES_HOME},
        )

        if result.returncode != 0:
            last_error = f"dispatch exit code {result.returncode}: {result.stderr.strip()}"
            logger.error("Dispatch failed: %s", last_error)
            return {"error": last_error}

        data = json.loads(result.stdout)
        dispatches += 1
        spawned = len(data.get("spawned", []))
        reclaimed = data.get("reclaimed", 0)
        tasks_spawned += spawned
        tasks_reclaimed += reclaimed

        if spawned or reclaimed:
            logger.info(
                "Dispatch #%d: spawned=%d reclaimed=%d",
                dispatches, spawned, reclaimed,
            )
        else:
            logger.debug("Dispatch #%d: no work", dispatches)

        return data

    except subprocess.TimeoutExpired:
        last_error = "dispatch timed out after 120s"
        logger.error(last_error)
        return {"error": last_error}
    except json.JSONDecodeError as e:
        last_error = f"dispatch output not valid JSON: {e}"
        logger.error("JSON parse error. stdout=%r",
                     result.stdout[:500] if 'result' in dir() else '')
        return {"error": last_error}
    except Exception as e:
        last_error = f"dispatch exception: {e}"
        logger.exception("Unexpected dispatch error")
        return {"error": last_error}


# ── Heartbeat ──

def get_active_worker_count() -> int:
    """Count currently running kanban tasks."""
    cmd = [
        HERMES_BIN, "kanban", "list",
        "--status", "running",
        "--json",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            env={**os.environ, "HERMES_HOME": HERMES_HOME},
        )
        if result.returncode == 0:
            tasks = json.loads(result.stdout)
            return len(tasks)
    except Exception:
        pass
    return -1


def post_heartbeat() -> bool:
    """POST heartbeat to dashboard /api/worker/heartbeat."""
    active = get_active_worker_count()
    uptime = int(time.time() - start_time)

    payload = {
        "running": True,
        "active_workers": active,
        "last_poll": last_poll_time,
        "dispatches": dispatches,
        "tasks_spawned": tasks_spawned,
        "uptime_seconds": uptime,
    }
    if last_error:
        payload["last_error"] = last_error

    url = f"{DASHBOARD_URL.rstrip('/')}/api/worker/heartbeat"
    data = json.dumps(payload).encode("utf-8")

    try:
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                logger.debug("Heartbeat OK (active=%d, uptime=%ds)", active, uptime)
                return True
            else:
                logger.warning("Heartbeat returned HTTP %d", resp.status)
                return False
    except urllib.error.URLError as e:
        logger.debug("Heartbeat failed (dashboard unreachable): %s", e)
        return False
    except Exception as e:
        logger.warning("Heartbeat error: %s", e)
        return False


# ── CLI ──

def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        description="Hermes Kanban Worker Poller Daemon",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s              Run the poller daemon\n"
            "  %(prog)s --dry-run    Run one dispatch tick and exit\n"
            "  %(prog)s --version    Print version and exit"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run one dispatch tick, print JSON result to stdout, and exit "
             "(no heartbeat, no loop)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Print version and exit",
    )
    return parser


# ── Main ──

def main(argv: Optional[list[str]] = None) -> None:
    global last_poll_time, running, trigger_now

    parser = build_parser()
    args = parser.parse_args(argv)

    # -- Dry-run mode: one dispatch tick, print JSON, exit --
    if args.dry_run:
        logger.info("DRY-RUN MODE -- running one dispatch tick")
        logger.info("Hermes home: %s", HERMES_HOME)

        try:
            subprocess.run(
                [HERMES_BIN, "kanban", "list", "--status", "ready", "--json"],
                capture_output=True, text=True, timeout=30,
                env={**os.environ, "HERMES_HOME": HERMES_HOME},
            )
            logger.info("Hermes CLI verified accessible")
        except Exception as e:
            logger.error("Cannot reach hermes CLI: %s", e)
            sys.exit(1)

        result = run_dispatch()
        print(json.dumps(result, indent=2))
        logger.info(
            "Dry-run complete -- dispatches=%d spawned=%d reclaimed=%d",
            dispatches, tasks_spawned, tasks_reclaimed,
        )
        sys.exit(0 if "error" not in result else 1)

    # -- Normal mode: poller daemon loop --
    logger.info(
        "Worker daemon starting -- poll=%ds max_spawns=%d dashboard=%s log=%s",
        POLL_INTERVAL, MAX_SPAWNS, DASHBOARD_URL, LOG_PATH,
    )
    logger.info("Hermes home: %s", HERMES_HOME)
    logger.info("PID: %d", os.getpid())

    try:
        subprocess.run(
            [HERMES_BIN, "kanban", "list", "--status", "ready", "--json"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "HERMES_HOME": HERMES_HOME},
        )
        logger.info("Hermes CLI verified accessible")
    except Exception as e:
        logger.error("Cannot reach hermes CLI: %s", e)
        sys.exit(1)

    while running:
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        last_poll_time = now_utc

        dispatch_result = run_dispatch()
        post_heartbeat()

        if "error" not in dispatch_result:
            for task in dispatch_result.get("spawned", []):
                logger.info(
                    "Spawned: task=%s assignee=%s",
                    task.get("task_id", "?"),
                    task.get("assignee", "?"),
                )

        if not running:
            break

        for _ in range(POLL_INTERVAL):
            if not running or trigger_now:
                trigger_now = False
                break
            time.sleep(1)

    logger.info(
        "Worker daemon stopped -- dispatches=%d spawned=%d reclaimed=%d uptime=%ds",
        dispatches, tasks_spawned, tasks_reclaimed,
        int(time.time() - start_time),
    )


if __name__ == "__main__":
    main()
