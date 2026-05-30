"""Chat module – Flask blueprint for the /chat page and API.

Bridges the Gardenia Dashboard to Hermes profiles via:
  1. WebSocket JSON-RPC to the Hermes Dashboard (port 9119, --tui) for the default profile.
  2. CLI subprocess (hermes -z) as a fallback for other profiles.

Session tokens are fetched once on module load and refreshed if they expire.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Optional

import requests
from flask import Blueprint, render_template, request, jsonify, current_app

logger = logging.getLogger(__name__)

chat_bp = Blueprint(
    "chat",
    __name__,
    template_folder="../templates",
    static_folder="../static",
)

# ── Configuration ────────────────────────────────────────────────────────────
HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
DASHBOARD_PORT = int(os.environ.get("HERMES_DASHBOARD_PORT", "9119"))
DASHBOARD_URL = f"http://127.0.0.1:{DASHBOARD_PORT}"
DASHBOARD_WS = f"ws://127.0.0.1:{DASHBOARD_PORT}/api/ws"
DEFAULT_PROFILE = "default"

# ── Token management ─────────────────────────────────────────────────────────
# The Hermes Dashboard generates an ephemeral session token on startup.
# We cache it and refresh if a request fails with 401.

_session_token: Optional[str] = None
_token_lock = threading.Lock()
_token_expires: float = 0.0


def _fetch_session_token() -> Optional[str]:
    """Scrape the session token from the dashboard's index page."""
    try:
        resp = requests.get(f"{DASHBOARD_URL}/", timeout=5)
        resp.raise_for_status()
        import re
        match = re.search(
            r'__HERMES_SESSION_TOKEN__\s*=\s*"([^"]+)"', resp.text
        )
        if match:
            return match.group(1)
        logger.warning("Token not found in dashboard HTML")
        return None
    except Exception as exc:
        logger.warning("Could not fetch dashboard token: %s", exc)
        return None


def _get_token() -> Optional[str]:
    """Return the cached session token, refreshing if needed."""
    global _session_token, _token_expires
    now = time.time()
    if _session_token and now < _token_expires:
        return _session_token

    with _token_lock:
        # Double-check after acquiring lock
        if _session_token and now < _token_expires:
            return _session_token
        token = _fetch_session_token()
        if token:
            _session_token = token
            _token_expires = now + 3600  # Refresh hourly
            logger.info("Hermes dashboard token refreshed")
        return token


def _invalidate_token() -> None:
    """Force a token refresh on next request."""
    global _session_token, _token_expires
    with _token_lock:
        _session_token = None
        _token_expires = 0


# ── Profile fetching ─────────────────────────────────────────────────────────

_profiles_cache: list[dict] = []
_profiles_cache_time: float = 0.0


def _fetch_profiles() -> list[dict]:
    """Fetch available Hermes profiles from the dashboard."""
    global _profiles_cache, _profiles_cache_time
    now = time.time()
    if _profiles_cache and now - _profiles_cache_time < 300:
        return _profiles_cache

    token = _get_token()
    if not token:
        logger.warning("No token available; using hardcoded profile list")
        return _hardcoded_profiles()

    try:
        resp = requests.get(
            f"{DASHBOARD_URL}/api/profiles",
            headers={"X-Hermes-Session-Token": token},
            timeout=10,
        )
        if resp.status_code == 401:
            _invalidate_token()
            return _hardcoded_profiles()
        resp.raise_for_status()
        data = resp.json()
        profiles = []
        for p in data.get("profiles", []):
            profiles.append({
                "id": p["name"],
                "name": p["name"].replace("-", " ").title(),
                "role": f"{p.get('provider', '?')} / {p.get('model', '?')}",
                "model": p.get("model", ""),
                "provider": p.get("provider", ""),
            })
        _profiles_cache = profiles
        _profiles_cache_time = now
        return profiles
    except Exception as exc:
        logger.warning("Could not fetch profiles: %s", exc)
        return _hardcoded_profiles()


def _hardcoded_profiles() -> list[dict]:
    """Fallback profile list when the dashboard is unreachable."""
    return [
        {"id": "default", "name": "Hermes", "role": "deepseek / deepseek-v4-pro",
         "model": "deepseek-v4-pro", "provider": "deepseek"},
        {"id": "lena", "name": "Lena", "role": "copilot / gpt-5.2",
         "model": "gpt-5.2", "provider": "copilot"},
        {"id": "marcus", "name": "Marcus", "role": "deepseek / deepseek-v4-pro",
         "model": "deepseek-v4-pro", "provider": "deepseek"},
        {"id": "soren", "name": "Soren", "role": "copilot / gemini-2.5-pro",
         "model": "gemini-2.5-pro", "provider": "copilot"},
        {"id": "kai", "name": "Kai", "role": "copilot / gpt-5-mini",
         "model": "gpt-5-mini", "provider": "copilot"},
    ]


# ── WebSocket chat (for the default profile) ─────────────────────────────────

def _ws_send(message: str, profile: str = "default") -> dict:
    """Send a message via WebSocket JSON-RPC to the Hermes Dashboard.

    Creates a session, submits the prompt, collects message.delta events,
    and returns the concatenated response.

    Currently only the default profile is supported via WS since the
    dashboard runs under that profile. Other profiles fall through to CLI.
    """
    if profile != DEFAULT_PROFILE:
        return {"ok": False, "error": f"WebSocket only supports '{DEFAULT_PROFILE}' profile; use CLI for '{profile}'"}

    token = _get_token()
    if not token:
        return {"ok": False, "error": "Cannot reach Hermes Dashboard (no session token). Is it running with --tui on port 9119?"}

    try:
        import websocket
    except ImportError:
        return {"ok": False, "error": "websocket-client not installed"}

    ws_url = f"{DASHBOARD_WS}?token={token}"

    try:
        ws = websocket.create_connection(ws_url, timeout=15)
    except Exception as exc:
        return {"ok": False, "error": f"WebSocket connection failed: {exc}"}

    try:
        # Drain the gateway.ready event
        _ws_drain_events(ws)

        # 1. Create session
        req = {"jsonrpc": "2.0", "id": "1", "method": "session.create", "params": {}}
        ws.send(json.dumps(req))
        resp = _ws_recv_rpc(ws, timeout=10)
        if resp is None or "error" in resp:
            err = resp.get("error", {}).get("message", "session.create failed") if resp else "no response"
            return {"ok": False, "error": f"Session creation failed: {err}"}

        session_id = resp["result"]["session_id"]

        # 2. Submit prompt
        req2 = {
            "jsonrpc": "2.0",
            "id": "2",
            "method": "prompt.submit",
            "params": {"session_id": session_id, "text": message},
        }
        ws.send(json.dumps(req2))
        ack = _ws_recv_rpc(ws, timeout=10)
        if ack is None:
            return {"ok": False, "error": "No response to prompt.submit"}

        # 3. Collect streaming events (longer recv timeout for slow models)
        full_text: list[str] = []
        start = time.time()
        ws.settimeout(30)  # deepseek can be slow to start generating
        while time.time() - start < 90:
            try:
                raw = ws.recv()
                msg = json.loads(raw)
                if msg.get("method") == "event":
                    evt = msg.get("params", {})
                    etype = evt.get("type", "")
                    if etype == "message.delta":
                        text = evt.get("payload", {}).get("text", "")
                        full_text.append(text)
                    elif etype == "message.complete":
                        break
                    elif etype == "error":
                        err_msg = evt.get("payload", {}).get("message", str(evt))
                        full_text.append(f"\n[Error: {err_msg}]")
                        break
                    # Other events: session.info, tool.*, thinking.* — skip silently
                # RPC responses with ids are acks — skip
            except websocket.WebSocketTimeoutException:
                # No more events, assume model finished (or very slow)
                if full_text:
                    break  # got some text, probably done
                # If no text yet, keep waiting — model is just slow
                continue
            except Exception:
                break

        reply = "".join(full_text).strip()
        if not reply:
            return {"ok": False, "error": "Received empty response from profile"}
        return {"ok": True, "reply": reply}

    except Exception as exc:
        logger.exception("WebSocket chat error")
        return {"ok": False, "error": f"WebSocket error: {exc}"}
    finally:
        try:
            ws.close()
        except Exception:
            pass


def _ws_drain_events(ws, timeout: float = 2.0) -> None:
    """Drain initial events (like gateway.ready) from the WebSocket."""
    import websocket as wslib
    try:
        ws.settimeout(timeout)
        while True:
            raw = ws.recv()
            msg = json.loads(raw)
            if msg.get("method") != "event":
                # Put it back? Can't; just drain all initial events
                pass
    except wslib.WebSocketTimeoutException:
        pass
    except Exception:
        pass


def _ws_recv_rpc(ws, timeout: float = 10.0):
    """Receive messages until we get a JSON-RPC response (one with an 'id')."""
    import websocket as wslib
    start = time.time()
    while time.time() - start < timeout:
        try:
            ws.settimeout(max(timeout - (time.time() - start), 0.5))
            raw = ws.recv()
            msg = json.loads(raw)
            if msg.get("method") == "event":
                continue  # skip events
            if "id" in msg:
                return msg
        except wslib.WebSocketTimeoutException:
            return None
        except Exception:
            return None
    return None


# ── CLI subprocess chat (fallback for non-default profiles) ──────────────────

HERMES_BIN = os.path.join(HERMES_HOME, "hermes-agent/venv/bin/hermes")


def _cli_send(message: str, profile: str) -> dict:
    """Send a message via hermes -z (one-shot) subprocess.

    Used for non-default profiles since the WebSocket dashboard is bound
    to the default profile.

    Note: hermes -z may exit with code 134 (SIGABRT) after successfully
    printing the response on stdout. This is a known cleanup issue in
    hermes-agent v0.14.0 and we handle it by checking stdout content first.
    """
    if not os.path.isfile(HERMES_BIN):
        return {"ok": False, "error": f"Hermes binary not found at {HERMES_BIN}"}

    env = os.environ.copy()
    env["HERMES_HOME"] = HERMES_HOME
    # Ensure the root .env is loaded for API keys (subprofiles may not
    # have their own keys and rely on shared root credentials).
    env.setdefault("DEEPSEEK_API_KEY", os.environ.get("DEEPSEEK_API_KEY", ""))
    env.setdefault("OPENROUTER_API_KEY", os.environ.get("OPENROUTER_API_KEY", ""))
    env.setdefault("GITHUB_COPILOT_TOKEN", os.environ.get("GITHUB_COPILOT_TOKEN", ""))

    cmd = [
        HERMES_BIN,
        "-z", message,
        "--profile", profile,
        "--accept-hooks",
        "--yolo",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            cwd=os.path.expanduser("~"),
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        # ── Parse stdout for the actual response ──────────────────────────
        # hermes -z output format:
        #   <banner lines...>
        #   ● <prompt echo>
        #   <actual response>
        #   (sometimes shell error noise like "Aborted" on exit)
        #
        # Strategy: find lines that look like the actual response
        # (not banner, not prompt echo, not shell noise).
        lines = stdout.split("\n")
        response_lines = []
        in_banner = True
        for line in lines:
            stripped = line.strip()
            # Skip banner / noise lines
            if not stripped:
                continue
            if any(noise in stripped for noise in [
                "Aborted", "unmonitored command", "/usr/bin/bash",
                "HERMES_HOME=", "timeout:", "dumped core",
            ]):
                continue
            if stripped.startswith("● ") or stripped.startswith("⚕ "):
                in_banner = False  # prompt echo line — response follows
                continue
            if in_banner and any(b in stripped for b in [
                "Hermes Agent v", "Available Tools", "Available Skills",
                "MCP Servers", "Welcome to Hermes", "Tip:",
                "Session:", "YOLO", "╭", "╰", "│", "Profile:",
            ]):
                continue
            # This looks like actual content
            response_lines.append(stripped)

        reply = "\n".join(response_lines).strip()

        # ── Check for API failure messages ────────────────────────────────
        if "API call failed" in stdout or "API call failed" in stderr:
            return {
                "ok": False,
                "error": f"Profile '{profile}' is currently unavailable (API error). "
                         "Try the 'default' profile or check provider connectivity.",
            }

        # ── Determine success ─────────────────────────────────────────────
        # Exit code 134 (SIGABRT) is normal for hermes -z — it's a cleanup
        # crash after the response is delivered. We treat as success if we
        # got meaningful output.
        if reply:
            return {"ok": True, "reply": reply}

        # No response — check for real errors
        error_msg = stderr or stdout or f"Exit code {result.returncode}"
        return {"ok": False, "error": error_msg[:500]}

    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Profile '{profile}' timed out after 120s"}
    except FileNotFoundError:
        return {"ok": False, "error": f"Hermes binary not found at {HERMES_BIN}"}
    except Exception as exc:
        logger.exception("CLI chat error")
        return {"ok": False, "error": f"CLI error: {exc}"}


# ── Gateway send (router) ────────────────────────────────────────────────────

def _gateway_send(profile: str, message: str) -> dict:
    """Route a message to the appropriate backend based on profile.

    - default profile → WebSocket (fast, streaming-capable)
    - other profiles → CLI subprocess (hermes -z)
    """
    if profile == DEFAULT_PROFILE:
        result = _ws_send(message, profile)
        if result["ok"]:
            return result
        # If WS fails, fall through to CLI
        logger.warning("WS send failed for default profile, falling back to CLI")
        return _cli_send(message, profile)

    return _cli_send(message, profile)


# ── Blueprint routes ─────────────────────────────────────────────────────────

@chat_bp.get("/chat")
def chat_page():
    """Render the chat UI."""
    return render_template("chat.html", title="Chat", active="chat")


@chat_bp.get("/api/chat/profiles")
def api_profiles():
    """Return the list of known chat profiles."""
    # Try to fetch live profiles from the dashboard
    try:
        profiles = _fetch_profiles()
    except Exception:
        profiles = _hardcoded_profiles()
    return jsonify({"profiles": profiles})


@chat_bp.post("/api/chat/send")
def api_send():
    """Accept a {profile, message} payload and return {reply}.

    Routes to WebSocket (default profile) or CLI subprocess (other profiles).
    When all backends are unavailable the response includes ``ok: false`` and a
    human-readable ``error`` so the UI can surface it gracefully.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"ok": False, "error": "Request body must be valid JSON"}), 400

    profile = data.get("profile")
    message = data.get("message")

    if not profile or not message:
        return jsonify({"ok": False, "error": "Both 'profile' and 'message' are required"}), 400

    result = _gateway_send(profile, message)
    return jsonify(result)


@chat_bp.get("/api/chat/status")
def api_status():
    """Health check for the chat module."""
    token = _get_token()
    return jsonify({
        "dashboard_url": DASHBOARD_URL,
        "dashboard_reachable": token is not None,
        "default_profile": DEFAULT_PROFILE,
    })
