"""Lightfield CRM module — Flask Blueprint for Lightfield CRM integration.

Routes:
  GET  /crm                    — Full CRM page (contacts, accounts, opportunities, etc.)
  GET  /api/crm/summary        — High-level summary counts
  GET  /api/crm/contacts       — List contacts
  GET  /api/crm/accounts       — List accounts
  GET  /api/crm/opportunities  — List opportunities
  GET  /api/crm/tasks          — List tasks
  GET  /api/crm/emails         — List emails
  GET  /api/crm/resource/<id>  — Single resource detail
"""

from __future__ import annotations

import logging
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request, current_app

from services.lightfield_client import get_client, resource_name

logger = logging.getLogger(__name__)

crm_bp = Blueprint(
    "crm",
    __name__,
    template_folder="../templates",
    static_folder="../static",
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _format_date(raw: str | None) -> str:
    """Format ISO date to friendly short form."""
    if not raw:
        return "—"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.strftime("%b %-d, %Y")
    except (ValueError, TypeError):
        return raw[:10] if len(raw) >= 10 else raw


def _field_value(fields: dict, key: str, default: str = "—") -> str:
    """Extract a human-readable value from a Lightfield field dict."""
    fv = fields.get(key, {})
    val = fv.get("value")
    if val is None:
        return default
    if isinstance(val, list):
        return ", ".join(str(v) for v in val) if val else default
    if isinstance(val, dict):
        # Address type
        parts = []
        for part in ("street", "city", "state", "postalCode", "country"):
            if val.get(part):
                parts.append(str(val[part]))
        return ", ".join(parts) if parts else default
    if isinstance(val, bool):
        return "Yes" if val else "No"
    return str(val)


def _simplify_resource(r: dict) -> dict:
    """Strip a Lightfield resource to display-relevant fields."""
    fields = r.get("fields", {})
    return {
        "id": r.get("id", ""),
        "name": resource_name(r),
        "email": _field_value(fields, "$email"),
        "phone": _field_value(fields, "$phone"),
        "title": _field_value(fields, "$title"),
        "company": _field_value(fields, "$website"),
        "industry": _field_value(fields, "$industry"),
        "status": _field_value(fields, "$accountStatus", "active"),
        "created": _format_date(r.get("createdAt")),
        "updated": _format_date(r.get("updatedAt")),
        "link": r.get("httpLink", ""),
    }


# ── Page routes ──────────────────────────────────────────────────────────────

@crm_bp.get("/crm")
def crm_page():
    """Render the CRM dashboard page."""
    return render_template("crm.html", title="CRM", active="crm")


# ── API routes ───────────────────────────────────────────────────────────────

@crm_bp.get("/api/crm/summary")
def crm_summary():
    """High-level counts across all CRM resources. Returns HTML for HTMX, JSON otherwise."""
    client = get_client()
    is_htmx = request.headers.get("HX-Request", "").lower() == "true"
    try:
        contacts = client.count_contacts()
        accounts = client.count_accounts()
        opportunities = client.count_opportunities()
        tasks_count = client.count_tasks()
        emails_count = client.count_emails()

        if is_htmx:
            return f"""<section id="crm-stats" class="stats"
         hx-get="/api/crm/summary"
         hx-trigger="every 120s"
         hx-swap="outerHTML">
  <div class="stat"><div class="label">Contacts</div><div class="value">{contacts}</div></div>
  <div class="stat"><div class="label">Accounts</div><div class="value">{accounts}</div></div>
  <div class="stat"><div class="label">Opportunities</div><div class="value">{opportunities}</div></div>
  <div class="stat"><div class="label">Tasks</div><div class="value">{tasks_count}</div></div>
  <div class="stat"><div class="label">Emails</div><div class="value">{emails_count}</div></div>
</section>"""

        return jsonify(
            {
                "contacts": contacts,
                "accounts": accounts,
                "opportunities": opportunities,
                "tasks": tasks_count,
                "emails": emails_count,
            }
        )
    except Exception as exc:
        logger.error("CRM summary failed: %s", exc)
        if is_htmx:
            return f"""<section id="crm-stats" class="stats"
         hx-get="/api/crm/summary"
         hx-trigger="every 120s"
         hx-swap="outerHTML">
  <div class="stat"><div class="label">Error</div><div class="value">—</div><div class="hint">CRM unreachable</div></div>
</section>"""
        return jsonify({"error": str(exc)}), 500


@crm_bp.get("/api/crm/contacts")
def crm_contacts():
    """List contacts. HTMX returns HTML table, otherwise JSON."""
    client = get_client()
    limit = request.args.get("limit", 25, type=int)
    is_htmx = request.headers.get("HX-Request", "").lower() == "true"
    try:
        contacts = client.list_contacts(limit=limit)
        simplified = [_simplify_resource(c) for c in contacts]

        if is_htmx:
            rows = ""
            for c in simplified:
                rows += f"""
  <tr>
    <td><strong>{c['name']}</strong></td>
    <td>{c['email']}</td>
    <td>{c['title'] or '—'}</td>
    <td>{c['company'] or '—'}</td>
    <td>{c['created']}</td>
    <td><a href="{c['link']}" target="_blank" class="btn btn-ghost btn-sm">↗</a></td>
  </tr>"""
            return f"""<div class="crm-table-wrap">
  <p class="crm-count">{len(simplified)} contacts</p>
  <table class="crm-table">
    <thead><tr><th>Name</th><th>Email</th><th>Title</th><th>Company</th><th>Created</th><th></th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""

        return jsonify(
            {
                "count": len(contacts),
                "contacts": simplified,
            }
        )
    except Exception as exc:
        logger.error("CRM contacts failed: %s", exc)
        if is_htmx:
            return f'<div class="crm-error">Error loading contacts: {exc}</div>'
        return jsonify({"error": str(exc)}), 500


@crm_bp.get("/api/crm/accounts")
def crm_accounts():
    """List accounts."""
    client = get_client()
    limit = request.args.get("limit", 25, type=int)
    is_htmx = request.headers.get("HX-Request", "").lower() == "true"
    try:
        accounts = client.list_accounts(limit=limit)
        simplified = [_simplify_resource(a) for a in accounts]

        if is_htmx:
            rows = ""
            for a in simplified:
                rows += f"""
  <tr>
    <td><strong>{a['name']}</strong></td>
    <td>{a['industry']}</td>
    <td>{a['email']}</td>
    <td>{a['phone']}</td>
    <td>{a['created']}</td>
    <td><a href="{a['link']}" target="_blank" class="btn btn-ghost btn-sm">↗</a></td>
  </tr>"""
            return f"""<div class="crm-table-wrap">
  <p class="crm-count">{len(simplified)} accounts</p>
  <table class="crm-table">
    <thead><tr><th>Name</th><th>Industry</th><th>Contact</th><th>Phone</th><th>Created</th><th></th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""

        return jsonify({"count": len(accounts), "accounts": simplified})
    except Exception as exc:
        logger.error("CRM accounts failed: %s", exc)
        if is_htmx:
            return f'<div class="crm-error">Error loading accounts: {exc}</div>'
        return jsonify({"error": str(exc)}), 500


@crm_bp.get("/api/crm/opportunities")
def crm_opportunities():
    """List opportunities."""
    client = get_client()
    limit = request.args.get("limit", 25, type=int)
    is_htmx = request.headers.get("HX-Request", "").lower() == "true"
    try:
        opps = client.list_opportunities(limit=limit)
        simplified = []
        for o in opps:
            fields = o.get("fields", {})
            simplified.append({
                "id": o.get("id", ""),
                "name": resource_name(o) or o.get("id", "—")[:16],
                "amount": _field_value(fields, "$amount", "—"),
                "stage": _field_value(fields, "$stage", "—"),
                "close_date": _field_value(fields, "$closeDate", "—"),
                "created": _format_date(o.get("createdAt")),
                "link": o.get("httpLink", ""),
            })

        if is_htmx:
            rows = ""
            for o in simplified:
                rows += f"""
  <tr>
    <td><strong>{o['name']}</strong></td>
    <td>{o['amount']}</td>
    <td>{o['stage']}</td>
    <td>{o['close_date']}</td>
    <td>{o['created']}</td>
    <td><a href="{o['link']}" target="_blank" class="btn btn-ghost btn-sm">↗</a></td>
  </tr>"""
            return f"""<div class="crm-table-wrap">
  <p class="crm-count">{len(simplified)} opportunities</p>
  <table class="crm-table">
    <thead><tr><th>Name</th><th>Amount</th><th>Stage</th><th>Close Date</th><th>Created</th><th></th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""

        return jsonify({"count": len(opps), "opportunities": simplified})
    except Exception as exc:
        logger.error("CRM opportunities failed: %s", exc)
        if is_htmx:
            return f'<div class="crm-error">Error loading opportunities: {exc}</div>'
        return jsonify({"error": str(exc)}), 500


@crm_bp.get("/api/crm/tasks")
def crm_tasks():
    """List tasks."""
    client = get_client()
    limit = request.args.get("limit", 25, type=int)
    is_htmx = request.headers.get("HX-Request", "").lower() == "true"
    try:
        tasks = client.list_tasks(limit=limit)
        simplified = []
        for t in tasks:
            fields = t.get("fields", {})
            simplified.append({
                "id": t.get("id", ""),
                "name": resource_name(t) or t.get("id", "—")[:16],
                "due": _field_value(fields, "$dueDate", "—"),
                "status": _field_value(fields, "$taskStatus", "open"),
                "assigned": _field_value(fields, "$assignee", "—"),
                "created": _format_date(t.get("createdAt")),
                "link": t.get("httpLink", ""),
            })

        if is_htmx:
            rows = ""
            for t in simplified:
                rows += f"""
  <tr>
    <td><strong>{t['name']}</strong></td>
    <td>{t['status']}</td>
    <td>{t['assigned']}</td>
    <td>{t['due']}</td>
    <td>{t['created']}</td>
    <td><a href="{t['link']}" target="_blank" class="btn btn-ghost btn-sm">↗</a></td>
  </tr>"""
            return f"""<div class="crm-table-wrap">
  <p class="crm-count">{len(simplified)} tasks</p>
  <table class="crm-table">
    <thead><tr><th>Task</th><th>Status</th><th>Assigned</th><th>Due</th><th>Created</th><th></th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""

        return jsonify({"count": len(tasks), "tasks": simplified})
    except Exception as exc:
        logger.error("CRM tasks failed: %s", exc)
        if is_htmx:
            return f'<div class="crm-error">Error loading tasks: {exc}</div>'
        return jsonify({"error": str(exc)}), 500


@crm_bp.get("/api/crm/emails")
def crm_emails():
    """List emails."""
    client = get_client()
    limit = request.args.get("limit", 25, type=int)
    is_htmx = request.headers.get("HX-Request", "").lower() == "true"
    try:
        emails = client.list_emails(limit=limit)
        simplified = []
        for e in emails:
            fields = e.get("fields", {})
            simplified.append({
                "id": e.get("id", ""),
                "subject": _field_value(fields, "$subject", "(no subject)"),
                "from": _field_value(fields, "$from", "—"),
                "to": _field_value(fields, "$to", "—"),
                "sent": _format_date(e.get("createdAt")),
                "link": e.get("httpLink", ""),
            })

        if is_htmx:
            rows = ""
            for e in simplified:
                rows += f"""
  <tr>
    <td><strong>{e['subject']}</strong></td>
    <td>{e['from']}</td>
    <td>{e['to']}</td>
    <td>{e['sent']}</td>
    <td><a href="{e['link']}" target="_blank" class="btn btn-ghost btn-sm">↗</a></td>
  </tr>"""
            return f"""<div class="crm-table-wrap">
  <p class="crm-count">{len(simplified)} emails</p>
  <table class="crm-table">
    <thead><tr><th>Subject</th><th>From</th><th>To</th><th>Sent</th><th></th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""

        return jsonify({"count": len(emails), "emails": simplified})
    except Exception as exc:
        logger.error("CRM emails failed: %s", exc)
        if is_htmx:
            return f'<div class="crm-error">Error loading emails: {exc}</div>'
        return jsonify({"error": str(exc)}), 500
