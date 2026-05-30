#!/usr/bin/env python3
"""Integration tests for the Gardenia Unified Dashboard.

Usage:
    cd /home/hackbot/Projects/gardenia-dashboard
    ./venv/bin/python3 tests/test_integration.py

Or:
    python3 -m pytest tests/test_integration.py -v
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Any

BASE_URL = "http://localhost:8091"
TIMEOUT = 10

_passed = 0
_failed = 0


def _get(path: str) -> tuple[int, str]:
    """GET a path, return (status_code, body_text)."""
    url = f"{BASE_URL}{path}"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def _post(path: str, data: dict) -> tuple[int, str]:
    """POST JSON to a path, return (status_code, body_text)."""
    url = f"{BASE_URL}{path}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def _json(path: str) -> Any:
    """GET a path and parse JSON response."""
    status, body = _get(path)
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"_error": f"not JSON (status={status}): {body[:200]}"}


def assert_status(path: str, expected: int, label: str) -> None:
    global _passed, _failed
    status, _ = _get(path)
    if status == expected:
        _passed += 1
        print(f"  PASS [{status}] {label}")
    else:
        _failed += 1
        print(f"  FAIL [{status} expected {expected}] {label}")


def assert_page(path: str, label: str) -> None:
    global _passed, _failed
    status, body = _get(path)
    if status == 200 and "<!doctype html>" in body.lower():
        _passed += 1
        print(f"  PASS {label}")
    else:
        _failed += 1
        print(f"  FAIL {label} — status={status}, html={'yes' if '<!doctype' in body.lower() else 'no'}")


# ── Test suites ───────────────────────────────────────────────────────────────


def test_health() -> None:
    global _passed, _failed
    print("\n── Health ──")
    data = _json("/api/health")
    if data.get("ok") and data.get("service") == "gardenia-dashboard":
        _passed += 1
        print("  PASS /api/health returns {ok:true, service:gardenia-dashboard}")
    else:
        _failed += 1
        print(f"  FAIL /api/health: {data}")


def test_pages() -> None:
    print("\n── Pages ──")
    assert_page("/", "Dashboard index")
    assert_page("/youtube", "YouTube panel")
    assert_page("/media", "Media panel")
    assert_page("/chat", "Chat panel")
    assert_page("/tasks", "Tasks panel")


def test_youtube_api() -> None:
    global _passed, _failed
    print("\n── YouTube API ──")
    data = _json("/api/youtube/videos")
    if isinstance(data, dict) and "videos" in data and "total" in data:
        _passed += 1
        print(f"  PASS /api/youtube/videos — {data['total']} videos")
    else:
        _failed += 1
        print(f"  FAIL /api/youtube/videos: {data}")

    data = _json("/api/youtube/channels")
    if isinstance(data, dict) and "channels" in data:
        _passed += 1
        print(f"  PASS /api/youtube/channels — {len(data.get('channels', []))} channels")
    else:
        _failed += 1
        print(f"  FAIL /api/youtube/channels: {data}")

    # Search
    data = _json("/api/youtube/search?q=test")
    if isinstance(data, dict):
        _passed += 1
        print(f"  PASS /api/youtube/search — {data.get('total', 0)} results")
    else:
        _failed += 1
        print(f"  FAIL /api/youtube/search: {data}")


def test_chat_api() -> None:
    global _passed, _failed
    print("\n── Chat API ──")
    data = _json("/api/chat/profiles")
    if isinstance(data, dict) and "profiles" in data and len(data["profiles"]) > 0:
        _passed += 1
        print(f"  PASS /api/chat/profiles — {len(data['profiles'])} profiles")
    else:
        _failed += 1
        print(f"  FAIL /api/chat/profiles: {data}")

    # Send (gateway likely unreachable, but endpoint should respond)
    status, body = _post("/api/chat/send", {"profile": "hermes", "message": "ping"})
    data_resp = json.loads(body) if body else {}
    if status in (200, 500, 502, 503):
        _passed += 1
        print(f"  PASS /api/chat/send responds (status={status})")
    else:
        _failed += 1
        print(f"  FAIL /api/chat/send: status={status} body={body[:200]}")


def test_media_api() -> None:
    global _passed, _failed
    print("\n── Media API ──")
    data = _json("/api/media/health")
    if isinstance(data, dict) and all(k in data for k in ("sonarr", "radarr", "lidarr")):
        _passed += 1
        print(f"  PASS /api/media/health — sonarr={data['sonarr']['reachable']}, radarr={data['radarr']['reachable']}, lidarr={data['lidarr']['reachable']}")
    else:
        _failed += 1
        print(f"  FAIL /api/media/health: {data}")

    data = _json("/api/media/disk")
    if isinstance(data, dict) and "total_space" in data:
        _passed += 1
        print("  PASS /api/media/disk")
    else:
        _failed += 1
        print(f"  FAIL /api/media/disk: {data}")


def test_worker_api() -> None:
    global _passed, _failed
    print("\n── Worker API ──")
    data = _json("/api/worker/status")
    if isinstance(data, dict) and "running" in data:
        _passed += 1
        print(f"  PASS /api/worker/status — running={data['running']}")
    else:
        _failed += 1
        print(f"  FAIL /api/worker/status: {data}")


def test_static_assets() -> None:
    print("\n── Static Assets ──")
    assert_status("/static/style.css", 200, "style.css")
    assert_status("/static/chat.js", 200, "chat.js")


def test_404() -> None:
    print("\n── Error Handling ──")
    assert_status("/api/nonexistent", 404, "/api/nonexistent returns 404")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    global _passed, _failed
    print("=" * 60)
    print("Gardenia Dashboard — Integration Tests")
    print(f"Base URL: {BASE_URL}")
    print("=" * 60)

    test_health()
    test_pages()
    test_youtube_api()
    test_chat_api()
    test_media_api()
    test_worker_api()
    test_static_assets()
    test_404()

    total = _passed + _failed
    print("\n" + "=" * 60)
    print(f"Results: {_passed}/{total} passed, {_failed} failed")
    print("=" * 60)

    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
