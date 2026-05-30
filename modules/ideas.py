"""Ideas module -- Flask blueprint for the Ideas Bank page.

Parses YTS analysis markdown files from /tmp/yts/data/analysis/ and extracts
takeaways/ideas across multiple sections. Provides search, filter, and save
functionality with a JSON-backed personal collection.

API endpoints:
  GET  /api/ideas         -- list all ideas (supports ?q=, ?section=, ?channel=)
  GET  /api/ideas/search  -- full-text search across ideas
  POST /api/ideas/save    -- save an idea to personal collection
  GET  /api/ideas/saved   -- list saved ideas
  DELETE /api/ideas/saved/<idea_id> -- remove a saved idea
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional

from flask import Blueprint, jsonify, render_template, request, current_app

logger = logging.getLogger(__name__)

ideas_bp = Blueprint(
    "ideas",
    __name__,
    template_folder="../templates",
    static_folder="../static",
)

# ── Configuration ────────────────────────────────────────────────────────────

YTS_DATA_DIR = Path("/tmp/yts/data")
ANALYSIS_DIR = YTS_DATA_DIR / "analysis"
SAVED_IDEAS_FILE = YTS_DATA_DIR / "saved_ideas.json"
TAGS_FILE = YTS_DATA_DIR / "idea_tags.json"

# Sections to extract from analysis files. Key = section name, value = tag label.
EXTRACT_SECTIONS: dict[str, str] = {
    "KEY TAKEAWAYS": "takeaway",
    "VIDEO IDEAS": "video-idea",
    "WHAT TO STEAL": "technique",
    "WISDOM TO IMPLEMENT": "wisdom",
    "CONTENT GAPS": "gap",
}

# ── Data loading ─────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _load_all_ideas() -> list[dict]:
    """Parse all analysis files and extract ideas from every relevant section.

    Returns a list of idea dicts, each with:
      id, text, section, tag, video_id, title, channel, date, category
    """
    if not ANALYSIS_DIR.exists():
        return []

    ideas: list[dict] = []

    for md_path in sorted(
        ANALYSIS_DIR.glob("*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        try:
            text = md_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        video_id = md_path.stem
        meta = _parse_metadata(text)

        for section_name, tag in EXTRACT_SECTIONS.items():
            bullets = _extract_section_bullets(text, section_name)
            for bullet in bullets:
                idea_id = _idea_hash(video_id, section_name, bullet)
                ideas.append({
                    "id": idea_id,
                    "text": bullet,
                    "section": section_name,
                    "tag": tag,
                    "video_id": video_id,
                    "title": meta.get("title", ""),
                    "channel": meta.get("channel", ""),
                    "date": meta.get("date", ""),
                    "category": meta.get("category", ""),
                })

    return ideas


def _parse_metadata(text: str) -> dict[str, str]:
    """Extract VIDEO METADATA block fields from an analysis markdown file."""
    meta: dict[str, str] = {}
    pattern = re.compile(
        r"VIDEO METADATA\s*\n(.*?)(?=\nSUMMARY\n|\nKEY TAKEAWAYS\n|\Z)", re.DOTALL
    )
    match = pattern.search(text)
    if not match:
        return meta

    for line in match.group(1).strip().split("\n"):
        line = line.strip()
        for field in ("title", "channel", "date", "category"):
            if line.lower().startswith(f"{field}:"):
                meta[field] = line.split(":", 1)[1].strip()
                break

    return meta


def _extract_section_bullets(text: str, section_name: str) -> list[str]:
    """Extract bullet points from a named section (case-insensitive).

    Sections are delimited by a header line containing the section name,
    and end at the next ## header, ---, or end of file.
    """
    bullets: list[str] = []

    # Build a regex that matches the section header followed by content
    # until the next ##, ---, or EOF
    pattern = re.compile(
        rf"{re.escape(section_name)}.*?\n(.*?)(?=\n##\s|\n---|\n\Z)",
        re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(text)
    if not match:
        return bullets

    content = match.group(1)
    for line in content.split("\n"):
        stripped = line.strip()
        # Match bullet lines: - text or * text or 1. text
        if stripped.startswith("- ") and len(stripped) > 3:
            bullets.append(stripped[2:].strip())
        elif stripped.startswith("* ") and len(stripped) > 3:
            bullets.append(stripped[2:].strip())
        # Numbered lists
        elif re.match(r"^\d+\.\s", stripped) and len(stripped) > 4:
            bullets.append(re.sub(r"^\d+\.\s+", "", stripped).strip())

    return bullets


def _idea_hash(video_id: str, section: str, text: str) -> str:
    """Generate a stable ID for an idea based on its content."""
    raw = f"{video_id}|{section}|{text}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _load_saved_ideas() -> list[dict]:
    """Load saved ideas from the JSON file."""
    try:
        if SAVED_IDEAS_FILE.exists():
            return json.loads(SAVED_IDEAS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load saved ideas: %s", exc)
    return []


def _save_saved_ideas(data: list[dict]) -> None:
    """Persist saved ideas to the JSON file."""
    SAVED_IDEAS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SAVED_IDEAS_FILE.write_text(
        json.dumps(data, indent=2, default=str), encoding="utf-8"
    )


def _load_tags() -> dict[str, list[str]]:
    """Load manual tags from JSON. Returns {idea_id: [tag1, tag2]}."""
    try:
        if TAGS_FILE.exists():
            return json.loads(TAGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load tags: %s", exc)
    return {}


def _save_tags(data: dict[str, list[str]]) -> None:
    """Persist manual tags to the JSON file."""
    TAGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TAGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _is_htmx() -> bool:
    """True if the current request was made by HTMX."""
    return request.headers.get("HX-Request", "").lower() == "true"


# ── Routes ───────────────────────────────────────────────────────────────────


@ideas_bp.get("/ideas")
def ideas_page():
    """Render the Ideas Bank page."""
    return render_template("ideas.html", title="Ideas Bank", active="ideas")


@ideas_bp.get("/api/ideas")
def api_ideas():
    """GET /api/ideas?q=search&section=takeaway&channel=Calum%20Johnson

    Returns all ideas, optionally filtered. JSON by default; HTML partial when HX-Request.
    """
    all_ideas = _load_all_ideas()

    # ── Filters ──
    q = request.args.get("q", "").strip().lower()
    section = request.args.get("section", "").strip().lower()
    channel = request.args.get("channel", "").strip().lower()

    filtered = all_ideas
    if q:
        filtered = [
            i for i in filtered
            if q in i["text"].lower()
            or q in i["title"].lower()
            or q in i["channel"].lower()
        ]
    if section:
        # Map friendly names: "takeaway" → "KEY TAKEAWAYS"
        reverse = {v: k for k, v in EXTRACT_SECTIONS.items()}
        section_name = reverse.get(section, section.upper())
        filtered = [i for i in filtered if i["section"].upper() == section_name.upper()]
    if channel:
        filtered = [i for i in filtered if channel in i["channel"].lower()]

    # Enrich with saved status and manual tags
    saved = {s["id"]: s for s in _load_saved_ideas()}
    manual_tags = _load_tags()

    for idea in filtered:
        idea["saved"] = idea["id"] in saved
        idea["manual_tags"] = manual_tags.get(idea["id"], [])

    # ── Stats ──
    channels_set = sorted({i["channel"] for i in all_ideas if i["channel"]})
    sections_set = sorted({i["section"] for i in all_ideas if i["section"]})

    if _is_htmx():
        return _render_idea_grid(filtered, len(all_ideas))

    return jsonify({
        "ok": True,
        "ideas": filtered,
        "total": len(filtered),
        "total_all": len(all_ideas),
        "channels": channels_set,
        "sections": sections_set,
        "stats": {
            "total_ideas": len(all_ideas),
            "total_videos": len({i["video_id"] for i in all_ideas}),
            "total_channels": len(channels_set),
        },
    })


@ideas_bp.get("/api/ideas/search")
def api_ideas_search():
    """GET /api/ideas/search?q=dopamine

    Full-text search across idea text, video titles, and channel names.
    """
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return jsonify({"ok": False, "error": "Query must be at least 2 characters"}), 400

    all_ideas = _load_all_ideas()
    q_lower = q.lower()

    results = [
        i for i in all_ideas
        if q_lower in i["text"].lower()
        or q_lower in i["title"].lower()
        or q_lower in i["channel"].lower()
    ]

    saved = {s["id"] for s in _load_saved_ideas()}
    manual_tags = _load_tags()

    for idea in results:
        idea["saved"] = idea["id"] in saved
        idea["manual_tags"] = manual_tags.get(idea["id"], [])

    if _is_htmx():
        return _render_idea_grid(results, len(all_ideas))

    return jsonify({
        "ok": True,
        "query": q,
        "results": results,
        "total": len(results),
    })


@ideas_bp.post("/api/ideas/save")
def api_ideas_save():
    """POST /api/ideas/save

    Body: {"id": "<idea_hash>", "note": "optional note"}

    Saves an idea to the personal collection. If already saved, updates the note.
    """
    data = request.get_json(silent=True) or {}
    idea_id = data.get("id", "").strip()
    note = data.get("note", "").strip()

    if not idea_id:
        return jsonify({"ok": False, "error": "idea 'id' is required"}), 400

    # Resolve idea from the corpus
    all_ideas = _load_all_ideas()
    idea = next((i for i in all_ideas if i["id"] == idea_id), None)
    if not idea:
        # Check if it's already saved (allows re-saving previously saved items)
        saved = _load_saved_ideas()
        existing = next((s for s in saved if s["id"] == idea_id), None)
        if not existing:
            return jsonify({"ok": False, "error": f"Idea {idea_id} not found in corpus"}), 404
        idea = existing

    saved = _load_saved_ideas()

    # Upsert
    saved_entry = next((s for s in saved if s["id"] == idea_id), None)
    if saved_entry:
        saved_entry["note"] = note
        saved_entry["saved_at"] = datetime.utcnow().isoformat()
    else:
        saved.append({
            "id": idea_id,
            "text": idea.get("text", ""),
            "section": idea.get("section", ""),
            "tag": idea.get("tag", ""),
            "video_id": idea.get("video_id", ""),
            "title": idea.get("title", ""),
            "channel": idea.get("channel", ""),
            "date": idea.get("date", ""),
            "note": note,
            "saved_at": datetime.utcnow().isoformat(),
        })

    _save_saved_ideas(saved)
    return jsonify({"ok": True, "saved": True, "id": idea_id})


@ideas_bp.get("/api/ideas/saved")
def api_ideas_saved():
    """GET /api/ideas/saved

    Returns the personal collection of saved ideas.
    """
    saved = _load_saved_ideas()
    saved.sort(key=lambda s: s.get("saved_at", ""), reverse=True)

    if _is_htmx():
        return _render_saved_grid(saved)

    return jsonify({"ok": True, "saved": saved, "total": len(saved)})


@ideas_bp.delete("/api/ideas/saved/<idea_id>")
def api_ideas_unsave(idea_id: str):
    """DELETE /api/ideas/saved/<idea_id>

    Removes an idea from the personal collection.
    """
    saved = _load_saved_ideas()
    before = len(saved)
    saved = [s for s in saved if s["id"] != idea_id]
    if len(saved) == before:
        return jsonify({"ok": False, "error": "Idea not found in saved collection"}), 404

    _save_saved_ideas(saved)
    return jsonify({"ok": True, "removed": True, "id": idea_id})


@ideas_bp.post("/api/ideas/tag")
def api_ideas_tag():
    """POST /api/ideas/tag

    Body: {"id": "<idea_hash>", "tags": ["tag1", "tag2"]}

    Sets manual tags for an idea. Replaces existing tags.
    """
    data = request.get_json(silent=True) or {}
    idea_id = data.get("id", "").strip()
    tags = data.get("tags", [])

    if not idea_id:
        return jsonify({"ok": False, "error": "idea 'id' is required"}), 400
    if not isinstance(tags, list):
        return jsonify({"ok": False, "error": "'tags' must be a list"}), 400

    all_tags = _load_tags()
    if tags:
        all_tags[idea_id] = tags
    else:
        all_tags.pop(idea_id, None)

    _save_tags(all_tags)
    return jsonify({"ok": True, "id": idea_id, "tags": tags})


@ideas_bp.get("/api/ideas/stats")
def api_ideas_stats():
    """GET /api/ideas/stats

    Returns aggregate stats for the ideas panel.
    """
    all_ideas = _load_all_ideas()
    saved = _load_saved_ideas()

    channels = sorted({i["channel"] for i in all_ideas if i["channel"]})
    sections = sorted({i["section"] for i in all_ideas if i["section"]})

    return jsonify({
        "ok": True,
        "stats": {
            "total_ideas": len(all_ideas),
            "saved_ideas": len(saved),
            "total_videos": len({i["video_id"] for i in all_ideas}),
            "total_channels": len(channels),
        },
        "channels": channels,
        "sections": sections,
    })


# ── HTML partial renderers ───────────────────────────────────────────────────


def _render_idea_grid(ideas: list[dict], total_all: int) -> str:
    """Render the idea cards grid as an HTML fragment."""
    if not ideas:
        return '<section id="ideas-grid"><div class="empty"><div class="icon">💭</div><div>No ideas match your filters.</div></div></section>'

    cards: list[str] = []
    for idea in ideas:
        tag_class = idea.get("tag", "")
        saved_mark = "★" if idea.get("saved") else "☆"
        saved_class = "saved" if idea.get("saved") else ""
        source_label = (
            f"{idea.get('title', 'Untitled')} | {idea.get('channel', 'Unknown')}"
        )
        date_str = idea.get("date", "")[:10] if idea.get("date") else ""

        cards.append(f"""<div class="idea-card" id="idea-{idea['id']}">
  <div class="idea-body">
    <div class="idea-text">💡 {idea['text']}</div>
    <div class="idea-meta">
      <span class="idea-tag tag-{tag_class}">{idea.get('section', '')}</span>
      <span class="idea-source" title="{source_label}">{idea.get('channel', 'Unknown')}</span>
      {f'<span class="idea-date">{date_str}</span>' if date_str else ''}
    </div>
  </div>
  <button class="idea-save-btn {saved_class}"
          onclick="toggleSaveIdea('{idea['id']}', this)"
          title="Save to collection">
    {saved_mark}
  </button>
</div>""")

    count_info = f"Showing {len(ideas)} of {total_all} ideas"
    return f"""<section id="ideas-grid">
  <div class="section-label">{count_info}</div>
  {"".join(cards)}
</section>"""


def _render_saved_grid(saved: list[dict]) -> str:
    """Render the saved ideas grid as an HTML fragment."""
    if not saved:
        return '<section id="saved-grid"><div class="empty"><div class="icon">📌</div><div>No saved ideas yet. Browse the Ideas Bank and save what inspires you.</div></div></section>'

    cards: list[str] = []
    for item in saved:
        source_label = f"{item.get('title', 'Untitled')} | {item.get('channel', 'Unknown')}"
        note = item.get("note", "")
        note_html = f'<div class="idea-note">{note}</div>' if note else ""

        cards.append(f"""<div class="idea-card saved-card" id="saved-{item['id']}">
  <div class="idea-body">
    <div class="idea-text">📌 {item['text']}</div>
    {note_html}
    <div class="idea-meta">
      <span class="idea-tag tag-{item.get('tag', '')}">{item.get('section', '')}</span>
      <span class="idea-source" title="{source_label}">{item.get('channel', 'Unknown')}</span>
    </div>
  </div>
  <button class="idea-save-btn saved"
          onclick="removeSavedIdea('{item['id']}', this)"
          title="Remove from collection">
    ✕
  </button>
</div>""")

    return f"""<section id="saved-grid">
  <div class="section-label">{len(saved)} saved ideas</div>
  {"".join(cards)}
</section>"""
