"""YouTube module -- Flask blueprint for YouTube analysis panel.

Reads YTS data from /tmp/yts/data/ (state.json + analysis/*.md) and exposes
API endpoints consumed by the youtube.html frontend via HTMX.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional

from flask import Blueprint, jsonify, render_template, request, current_app

logger = logging.getLogger(__name__)

youtube_bp = Blueprint(
    "youtube",
    __name__,
    template_folder="../templates",
    static_folder="../static",
)

# ── Configuration ────────────────────────────────────────────────────────────
YTS_DATA_DIR = Path("/tmp/yts/data")
STATE_FILE = YTS_DATA_DIR / "state.json"
ANALYSIS_DIR = YTS_DATA_DIR / "analysis"

# ── Data loading (cached for 60s to avoid re-reading on every request) ───────


@lru_cache(maxsize=1)
def _load_state() -> dict:
    """Load the YTS state.json. Cached for 1 call cycle."""
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load %s: %s", STATE_FILE, exc)
        return {"processed": {}}


def _parse_analysis(video_id: str) -> dict:
    """Parse an analysis markdown file into structured sections.

    Returns dict with keys: title, channel, summary, takeaways, date, category.
    """
    md_path = ANALYSIS_DIR / f"{video_id}.md"
    if not md_path.exists():
        return {}

    text = md_path.read_text(encoding="utf-8", errors="replace")
    result: dict = {
        "video_id": video_id,
        "title": "",
        "channel": "",
        "date": "",
        "category": "",
        "summary": "",
        "takeaways": [],
    }

    # --- Video Metadata block ---
    meta_pattern = re.compile(
        r"VIDEO METADATA\s*\n(.*?)(?=\nSUMMARY\n|\nKEY TAKEAWAYS\n|\Z)", re.DOTALL
    )
    meta_match = meta_pattern.search(text)
    if meta_match:
        meta_text = meta_match.group(1)
        for line in meta_text.strip().split("\n"):
            line = line.strip()
            if line.lower().startswith("title:"):
                result["title"] = line.split(":", 1)[1].strip()
            elif line.lower().startswith("channel:"):
                result["channel"] = line.split(":", 1)[1].strip()
            elif line.lower().startswith("date:"):
                result["date"] = line.split(":", 1)[1].strip()
            elif line.lower().startswith("category:"):
                result["category"] = line.split(":", 1)[1].strip()

    # --- Summary block ---
    summary_pattern = re.compile(
        r"\nSUMMARY\s*\n(.*?)(?=\nKEY TAKEAWAYS\n|\nVIDEO METADATA\n|\nWHAT WORKED\n|\Z)", re.DOTALL
    )
    summary_match = summary_pattern.search(text)
    if summary_match:
        result["summary"] = summary_match.group(1).strip()

    # --- Key Takeaways block ---
    takeaways_pattern = re.compile(
        r"\nKEY TAKEAWAYS\s*\n(.*?)(?=\nWHAT WORKED\n|\nWHAT'S REPLICABLE\n|\nACTION ITEMS\n|\Z)", re.DOTALL
    )
    takeaways_match = takeaways_pattern.search(text)
    if takeaways_match:
        raw = takeaways_match.group(1).strip()
        result["takeaways"] = [
            t.strip("- ").strip()
            for t in raw.split("\n")
            if t.strip().startswith("-")
        ]

    return result


def _get_summary_excerpt(video_id: str, max_chars: int = 200) -> str:
    """Get a short excerpt from the analysis summary."""
    parsed = _parse_analysis(video_id)
    summary = parsed.get("summary", "")
    if not summary:
        return ""
    if len(summary) <= max_chars:
        return summary
    return summary[:max_chars].rsplit(" ", 1)[0] + "…"


def _build_video_list(
    limit: int = 50, offset: int = 0, channel: Optional[str] = None
) -> tuple[list[dict], int]:
    """Build a paginated, filterable list of videos with excerpts.

    Returns (videos, total_count).
    """
    state = _load_state()
    processed = state.get("processed", {})

    videos = []
    for vid, info in processed.items():
        if channel and info.get("channel", "") != channel:
            continue
        videos.append({
            "video_id": vid,
            "title": info.get("title", ""),
            "channel": info.get("channel", ""),
            "processed_at": info.get("processed_at", ""),
            "source": info.get("source", ""),
            "summary_excerpt": _get_summary_excerpt(vid),
        })

    # Sort by processed_at descending
    videos.sort(key=lambda v: v["processed_at"] or "", reverse=True)
    total = len(videos)
    page = videos[offset : offset + limit]

    return page, total


def _build_channel_stats() -> list[dict]:
    """Aggregate channel-level statistics from state.json."""
    state = _load_state()
    processed = state.get("processed", {})

    channels: dict[str, dict] = {}
    for vid, info in processed.items():
        name = info.get("channel", "Unknown")
        if name not in channels:
            channels[name] = {"name": name, "video_count": 0, "latest": ""}
        channels[name]["video_count"] += 1
        ts = info.get("processed_at", "")
        if ts and ts > channels[name]["latest"]:
            channels[name]["latest"] = ts

    result = sorted(channels.values(), key=lambda c: c["video_count"], reverse=True)
    return result


def _search_analyses(query: str, max_results: int = 20) -> list[dict]:
    """Search analysis markdown files for a query string.

    Returns list of video dicts with relevance snippets.
    """
    q_lower = query.lower()
    results = []

    if not ANALYSIS_DIR.exists():
        return results

    for md_path in sorted(ANALYSIS_DIR.glob("*.md")):
        video_id = md_path.stem
        try:
            text = md_path.read_text(encoding="utf-8", errors="replace").lower()
        except Exception:
            continue

        if q_lower not in text:
            continue

        # Build a snippet around the first match
        idx = text.find(q_lower)
        start = max(0, idx - 80)
        end = min(len(text), idx + len(query) + 120)
        snippet = "…" + text[start:end].replace("\n", " ").strip() + "…"

        parsed = _parse_analysis(video_id)
        results.append({
            "video_id": video_id,
            "title": parsed.get("title", ""),
            "channel": parsed.get("channel", ""),
            "snippet": snippet,
            "summary": parsed.get("summary", "")[:300],
        })

        if len(results) >= max_results:
            break

    return results


# ── Helpers ──────────────────────────────────────────────────────────────────


def _is_htmx() -> bool:
    """True if the current request was made by HTMX."""
    return request.headers.get("HX-Request", "").lower() == "true"


def _format_date(iso_str: str) -> str:
    """Format ISO datetime string to a short human-readable form."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y")
    except (ValueError, TypeError):
        return iso_str[:10] if len(iso_str) >= 10 else iso_str


# ── Routes ───────────────────────────────────────────────────────────────────


@youtube_bp.get("/youtube")
def youtube_page():
    """Render the YouTube analysis panel."""
    return render_template("youtube.html", title="YouTube", active="youtube")


@youtube_bp.get("/api/youtube/videos")
def api_videos():
    """GET /api/youtube/videos?limit=50&offset=0&channel=All%20In%20Podcast

    Returns paginated video list. JSON by default; HTML partial when HX-Request.
    """
    try:
        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)
        channel = request.args.get("channel", None, type=str)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid query parameters"}), 400

    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    videos, total = _build_video_list(limit=limit, offset=offset, channel=channel)

    if _is_htmx():
        return _render_video_grid(videos, total, channel)

    return jsonify({
        "ok": True,
        "videos": videos,
        "total": total,
        "offset": offset,
        "limit": limit,
    })


@youtube_bp.get("/api/youtube/channels")
def api_channels():
    """GET /api/youtube/channels

    Returns channel-level statistics. JSON by default; HTML partial when HX-Request.
    """
    channels = _build_channel_stats()
    total_videos = sum(c["video_count"] for c in channels)
    total_channels = len(channels)

    # Find most active channel
    most_active = channels[0]["name"] if channels else "—"

    if _is_htmx():
        return _render_channel_stats(total_videos, total_channels, most_active)

    return jsonify({
        "ok": True,
        "channels": channels,
        "total_channels": total_channels,
        "total_videos": total_videos,
    })


@youtube_bp.get("/api/youtube/search")
def api_search():
    """GET /api/youtube/search?q=dopamine

    Searches analysis markdown files. JSON by default; HTML partial when HX-Request.
    """
    q = request.args.get("q", "").strip()
    if not q:
        if _is_htmx():
            return "", 200
        return jsonify({"ok": False, "error": "Query parameter 'q' is required"}), 400

    if len(q) < 2:
        if _is_htmx():
            return '<div class="yt-search-empty muted">Type at least 2 characters…</div>', 200
        return jsonify({"ok": False, "error": "Query must be at least 2 characters"}), 400

    results = _search_analyses(q)

    if _is_htmx():
        return _render_search_results(results, q)

    return jsonify({
        "ok": True,
        "query": q,
        "results": results,
        "total": len(results),
    })


@youtube_bp.get("/api/youtube/channel-filter")
def api_channel_filter():
    """GET /api/youtube/channel-filter

    Returns HTML partial for the channel filter chips. HTMX-only.
    """
    channels = _build_channel_stats()
    return _render_channel_filter(channels)


# ── HTML partial renderers ────────────────────────────────────────────────────


def _render_channel_stats(total_videos: int, total_channels: int, most_active: str) -> str:
    """Render the stats bar as an HTML fragment."""
    return f"""<section id="yt-stats" class="stats">
  <div class="stat"><div class="label">Videos</div><div class="value">{total_videos}</div><div class="hint">Analyzed from YTS</div></div>
  <div class="stat"><div class="label">Channels</div><div class="value">{total_channels}</div><div class="hint">Active sources</div></div>
  <div class="stat"><div class="label">Most Active</div><div class="value" style="font-size:16px">{most_active}</div><div class="hint">Top channel</div></div>
</section>"""


def _render_channel_filter(channels: list[dict]) -> str:
    """Render the channel filter chips as an HTML fragment."""
    chips = ['<span class="filter-label">Filter by channel:</span>']
    chips.append(
        '<span class="channel-chip active" data-channel="" onclick="filterByChannel(\'\')">All</span>'
    )
    for ch in channels[:15]:  # cap at 15 visible chips
        name = ch["name"]
        count = ch["video_count"]
        chips.append(
            f'<span class="channel-chip" data-channel="{name}" '
            f'onclick="filterByChannel(\'{name}\')">{name} ({count})</span>'
        )
    return f'<div id="yt-channel-filter" class="yt-channel-bar">{" ".join(chips)}</div>'


def _render_video_grid(videos: list[dict], total: int, channel: str | None = None) -> str:
    """Render the video card grid as an HTML fragment."""
    if not videos:
        return '<section id="yt-videos"><div class="empty"><div class="icon">🎬</div><div>No videos found.</div></div></section>'

    cards = []
    for v in videos:
        vid = v["video_id"]
        title = v["title"] or "Untitled"
        channel_name = v["channel"] or "Unknown"
        excerpt = v.get("summary_excerpt", "") or "No summary available."
        date = _format_date(v.get("processed_at", ""))

        cards.append(f"""<div class="yt-video-card" onclick="loadVideoDetail('{vid}')">
      <div class="vc-title">{title}</div>
      <div class="vc-channel">{channel_name}</div>
      <div class="vc-excerpt">{excerpt}</div>
      <div class="vc-meta">
        <span class="vc-date">{date}</span>
        <span class="vc-source">{v.get("source", "")}</span>
      </div>
    </div>""")

    count_info = f"{total} video{'s' if total != 1 else ''}"
    if channel:
        count_info += f" from {channel}"

    return f"""<section id="yt-videos">
  <div class="yt-video-count muted">{count_info}</div>
  <div class="yt-video-grid">
    {"".join(cards)}
  </div>
</section>"""


def _render_search_results(results: list[dict], query: str) -> str:
    """Render search results as an HTML fragment."""
    if not results:
        return f'<div class="yt-search-results"><div class="yt-search-result muted">No results for "{query}".</div></div>'

    items = []
    for r in results:
        vid = r["video_id"]
        title = r.get("title") or "Untitled"
        channel_name = r.get("channel") or "Unknown"
        snippet = r.get("snippet", "")

        items.append(f"""<div class="yt-search-result" onclick="loadVideoDetail('{vid}')">
      <div class="sr-title">{title}</div>
      <div class="sr-channel">{channel_name}</div>
      <div class="sr-snippet">{snippet}</div>
    </div>""")

    return f'<div id="yt-search-results" class="yt-search-results">{"".join(items)}</div>'


@youtube_bp.get("/api/youtube/video/<video_id>")
def api_video_detail(video_id: str):
    """GET /api/youtube/video/7VxeyTfhBM8

    Returns full parsed analysis for a single video.
    """
    # Basic validation: video_id should be 11 chars typical YouTube ID
    if not re.match(r"^[\w\-]{5,15}$", video_id):
        return jsonify({"ok": False, "error": "Invalid video ID format"}), 400

    parsed = _parse_analysis(video_id)
    if not parsed or not parsed.get("title"):
        return jsonify({"ok": False, "error": f"No analysis found for {video_id}"}), 404

    # Enrich with state metadata
    state = _load_state()
    meta = state.get("processed", {}).get(video_id, {})
    parsed["processed_at"] = meta.get("processed_at", "")
    parsed["source"] = meta.get("source", "")

    return jsonify({"ok": True, "video": parsed})


@youtube_bp.post("/api/youtube/refresh")
def api_refresh():
    """POST /api/youtube/refresh

    Bust the state cache so the next API call re-reads from disk.
    """
    _load_state.cache_clear()
    return jsonify({"ok": True, "message": "Cache cleared. Next request will reload from disk."})
