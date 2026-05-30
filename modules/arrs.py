"""*arr Media module — Flask Blueprint for Sonarr / Radarr / Lidarr integration.

Routes:
  GET  /media                     — Full media page (tabs, health, disk)
  GET  /api/media/health          — Combined health status for all 3 services
  GET  /api/media/disk            — Combined disk space
  GET  /api/media/sonarr/calendar — Upcoming episodes
  GET  /api/media/sonarr/wanted   — Missing/wanted episodes
  GET  /api/media/sonarr/queue    — Active downloads
  GET  /api/media/radarr/calendar — Upcoming movies
  GET  /api/media/radarr/wanted   — Missing/wanted movies
  GET  /api/media/lidarr/wanted   — Missing/wanted albums
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from flask import Blueprint, jsonify, render_template, request

from services.arr_client import sonarr, radarr, lidarr, get_combined_health, get_combined_disk

logger = logging.getLogger(__name__)

arrs_bp = Blueprint(
    "arrs",
    __name__,
    template_folder="../templates",
    static_folder="../static",
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _format_date(raw: str | None) -> str:
    """Format an ISO date string into a friendly short form e.g. 'Mon Jun 2'."""
    if not raw:
        return "—"
    try:
        # *arr uses "2026-06-02" or ISO 8601 with timezone
        raw_clean = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw_clean)
        return dt.strftime("%a %b %-d")
    except (ValueError, TypeError):
        return raw[:10] if len(raw) >= 10 else raw

def _item_label(service: str, item: dict[str, Any]) -> str:
    """Return a human-readable label for a calendar/wanted/queue item."""
    if service == "sonarr":
        series = item.get("series", {}).get("title", "") or item.get("title", "")
        ep = item.get("title", "")
        season = item.get("seasonNumber", "")
        episode = item.get("episodeNumber", "")
        return f"{series} — {ep or f'S{season:02d}E{episode:02d}'}"
    elif service == "radarr":
        return item.get("title", "Unknown Movie")
    elif service == "lidarr":
        artist = item.get("artist", {}).get("artistName", "") or item.get("artistName", "")
        album = item.get("title", "")
        return f"{artist} — {album}" if artist else album
    return item.get("title", "—")


def _item_date(item: dict[str, Any]) -> str:
    """Best-effort date extraction from a *arr item."""
    # Try multiple date fields
    for field in ("airDateUtc", "airDate", "inCinemas", "physicalRelease", "digitalRelease", "releaseDate", "added"):
        raw = item.get(field)
        if raw:
            return _format_date(raw)
    return "—"


def _item_status(item: dict[str, Any]) -> str:
    """Human-readable status for an item."""
    if item.get("hasFile"):
        return "downloaded"
    if item.get("monitored") is False:
        return "unmonitored"
    status = item.get("status", "")
    if status:
        return status.lower()
    if item.get("downloading"):
        return "downloading"
    return "missing"


# ── Page route ───────────────────────────────────────────────────────────────

@arrs_bp.get("/media")
def media_page():
    """Render the full media management page."""
    return render_template("media.html", title="Media", active="media")


# ── Health & disk ────────────────────────────────────────────────────────────

@arrs_bp.get("/api/media/health")
def api_media_health():
    """GET /api/media/health — status of all three *arr services."""
    return jsonify(get_combined_health())


@arrs_bp.get("/api/media/disk")
def api_media_disk():
    """GET /api/media/disk — combined disk space."""
    return jsonify(get_combined_disk())


# ── Sonarr ───────────────────────────────────────────────────────────────────

@arrs_bp.get("/api/media/sonarr/calendar")
def api_sonarr_calendar():
    """Upcoming episodes (7 days)."""
    end = (date.today() + timedelta(days=7)).isoformat()
    raw = sonarr.get_calendar(
        start=date.today().isoformat(),
        end=end,
    )
    items = [
        {
            "label": _item_label("sonarr", item),
            "date": _item_date(item),
            "status": _item_status(item),
            "series": item.get("series", {}).get("title", ""),
            "episode": item.get("title", ""),
            "season": item.get("seasonNumber"),
            "episodeNumber": item.get("episodeNumber"),
            "hasFile": item.get("hasFile", False),
        }
        for item in raw
    ]
    return jsonify({"items": items, "count": len(items)})


@arrs_bp.get("/api/media/sonarr/wanted")
def api_sonarr_wanted():
    """Missing / wanted episodes."""
    raw = sonarr.get_wanted()
    items = [
        {
            "label": _item_label("sonarr", item),
            "date": _item_date(item),
            "status": _item_status(item),
            "series": item.get("series", {}).get("title", ""),
            "episode": item.get("title", ""),
            "season": item.get("seasonNumber"),
            "episodeNumber": item.get("episodeNumber"),
        }
        for item in raw
    ]
    return jsonify({"items": items, "count": len(items)})


@arrs_bp.get("/api/media/sonarr/queue")
def api_sonarr_queue():
    """Active Sonarr downloads."""
    raw = sonarr.get_queue()
    items = [
        {
            "label": _item_label("sonarr", item),
            "status": item.get("status", "").lower(),
            "size": item.get("size", 0),
            "sizeLeft": item.get("sizeleft", 0),
            "timeleft": item.get("timeleft", "—"),
            "progress": (
                round((1 - item.get("sizeleft", 0) / max(item.get("size", 1), 1)) * 100, 1)
                if item.get("size")
                else 0
            ),
        }
        for item in raw
    ]
    return jsonify({"items": items, "count": len(items)})


# ── HTML partial routes (return rendered HTML for HTMX) ─────────────────────

@arrs_bp.get("/api/media/health/bar")
def api_media_health_bar():
    """HTML partial: health status pills."""
    services = get_combined_health()
    return render_template("components/health_bar.html", services=services)


@arrs_bp.get("/api/media/disk/bar")
def api_media_disk_bar():
    """HTML partial: disk space bar."""
    data = get_combined_disk()
    return render_template("components/disk_bar.html", data=data)


@arrs_bp.get("/api/media/sonarr/panel")
def api_sonarr_panel():
    """HTML partial: Sonarr panel (calendar + wanted + queue)."""
    end = (date.today() + timedelta(days=7)).isoformat()
    calendar_raw = sonarr.get_calendar(start=date.today().isoformat(), end=end)
    wanted_raw = sonarr.get_wanted()
    queue_raw = sonarr.get_queue()

    calendar = [
        {"label": _item_label("sonarr", i), "date": _item_date(i), "status": _item_status(i)}
        for i in calendar_raw
    ]
    wanted = [
        {"label": _item_label("sonarr", i), "date": _item_date(i), "status": _item_status(i)}
        for i in wanted_raw
    ]
    queue = [
        {
            "label": _item_label("sonarr", i),
            "status": i.get("status", "").lower(),
            "progress": round(
                (1 - i.get("sizeleft", 0) / max(i.get("size", 1), 1)) * 100, 1
            ) if i.get("size") else 0,
        }
        for i in queue_raw
    ]
    return render_template(
        "components/media_panel.html",
        service="sonarr",
        calendar=calendar if calendar else None,
        wanted=wanted if wanted else None,
        queue=queue if queue else None,
    )


@arrs_bp.get("/api/media/radarr/panel")
def api_radarr_panel():
    """HTML partial: Radarr panel (calendar + wanted)."""
    end = (date.today() + timedelta(days=30)).isoformat()
    calendar_raw = radarr.get_calendar(start=date.today().isoformat(), end=end)
    wanted_raw = radarr.get_wanted()

    calendar = [
        {"label": _item_label("radarr", i), "date": _item_date(i), "status": _item_status(i)}
        for i in calendar_raw
    ]
    wanted = [
        {"label": _item_label("radarr", i), "date": _item_date(i), "status": _item_status(i)}
        for i in wanted_raw
    ]
    return render_template(
        "components/media_panel.html",
        service="radarr",
        calendar=calendar if calendar else None,
        wanted=wanted if wanted else None,
        queue=None,
    )


@arrs_bp.get("/api/media/lidarr/panel")
def api_lidarr_panel():
    """HTML partial: Lidarr panel (wanted albums)."""
    wanted_raw = lidarr.get_wanted()
    wanted = [
        {"label": _item_label("lidarr", i), "date": _item_date(i), "status": _item_status(i)}
        for i in wanted_raw
    ]
    return render_template(
        "components/media_panel.html",
        service="lidarr",
        calendar=None,
        wanted=wanted if wanted else None,
        queue=None,
    )


# ── Radarr ───────────────────────────────────────────────────────────────────

@arrs_bp.get("/api/media/radarr/calendar")
def api_radarr_calendar():
    """Upcoming movies."""
    end = (date.today() + timedelta(days=30)).isoformat()
    raw = radarr.get_calendar(
        start=date.today().isoformat(),
        end=end,
    )
    items = [
        {
            "label": _item_label("radarr", item),
            "date": _item_date(item),
            "status": _item_status(item),
            "title": item.get("title", ""),
            "year": item.get("year"),
            "hasFile": item.get("hasFile", False),
            "inCinemas": _format_date(item.get("inCinemas", "")),
        }
        for item in raw
    ]
    return jsonify({"items": items, "count": len(items)})


@arrs_bp.get("/api/media/radarr/wanted")
def api_radarr_wanted():
    """Missing / wanted movies."""
    raw = radarr.get_wanted()
    items = [
        {
            "label": _item_label("radarr", item),
            "date": _item_date(item),
            "status": _item_status(item),
            "title": item.get("title", ""),
            "year": item.get("year"),
        }
        for item in raw
    ]
    return jsonify({"items": items, "count": len(items)})


# ── Lidarr ───────────────────────────────────────────────────────────────────

@arrs_bp.get("/api/media/lidarr/wanted")
def api_lidarr_wanted():
    """Missing / wanted albums."""
    raw = lidarr.get_wanted()
    items = [
        {
            "label": _item_label("lidarr", item),
            "date": _item_date(item),
            "status": _item_status(item),
            "artist": item.get("artist", {}).get("artistName", ""),
            "album": item.get("title", ""),
        }
        for item in raw
    ]
    return jsonify({"items": items, "count": len(items)})
