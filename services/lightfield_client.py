"""Lightfield CRM API client.

Handles authentication, version headers, pagination, and provides
typed access to contacts, accounts, opportunities, tasks, and emails.

API discovered at https://backend.lightfield.app — Lightfield-Version: 2026-03-01
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

API_URL = os.environ.get("LIGHTFIELD_API_URL", "https://backend.lightfield.app")
API_KEY = os.environ.get(
    "LIGHTFIELD_API_KEY",
    "sk_lf_0_fwKICNC9SvX_Ev1ne0Ow46tGt6-JtQ4pB_IsVINu66FdkzY0ifSOViGFAqhnCdg1",
)
API_VERSION = os.environ.get("LIGHTFIELD_API_VERSION", "2026-03-01")
DEFAULT_LIMIT = 25  # API max per page
MAX_LIMIT = 25

# ── Session management ───────────────────────────────────────────────────────

class LightfieldClient:
    """Stateless HTTP client for the Lightfield CRM API."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {API_KEY}",
                "Lightfield-Version": API_VERSION,
                "Accept": "application/json",
            }
        )

    def _get(self, path: str, **params: Any) -> dict:
        """GET a paginated list endpoint. Returns full response dict."""
        # Cap limit to API max
        if "limit" in params:
            params["limit"] = min(params["limit"], MAX_LIMIT)
        url = f"{API_URL}{path}"
        resp = self._session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _count(self, path: str) -> int:
        """Get total count for a resource type (fetches 1 item, returns totalCount)."""
        data = self._get(path, limit=1)
        return data.get("totalCount", 0)

    def _get_one(self, path: str) -> dict:
        """GET a single resource. Returns the resource dict."""
        url = f"{API_URL}{path}"
        resp = self._session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _list_all(self, path: str, limit: int = DEFAULT_LIMIT) -> tuple[list[dict], int]:
        """Paginate through all results. Returns (all_items, total_count)."""
        all_items: list[dict] = []
        offset = 0
        while True:
            data = self._get(path, limit=limit, offset=offset)
            items = data.get("data", [])
            total = data.get("totalCount", 0)
            all_items.extend(items)
            if len(items) < limit:
                break
            offset += limit
        return all_items, total

    # ── Contacts ─────────────────────────────────────────────────────────────

    def list_contacts(self, limit: int = DEFAULT_LIMIT, offset: int = 0) -> list[dict]:
        """List contacts with their fields."""
        data = self._get("/v1/contacts", limit=limit, offset=offset)
        return data.get("data", [])

    def all_contacts(self, limit: int = DEFAULT_LIMIT) -> tuple[list[dict], int]:
        """Fetch ALL contacts via pagination. Returns (items, total_count)."""
        return self._list_all("/v1/contacts", limit=limit)

    def get_contact(self, contact_id: str) -> dict:
        """Get a single contact by ID."""
        return self._get_one(f"/v1/contacts/{contact_id}")

    def count_contacts(self) -> int:
        """Get total contact count (fast — fetches 1 item)."""
        return self._count("/v1/contacts")

    # ── Accounts ─────────────────────────────────────────────────────────────

    def list_accounts(self, limit: int = DEFAULT_LIMIT, offset: int = 0) -> list[dict]:
        """List accounts (companies/organizations)."""
        data = self._get("/v1/accounts", limit=limit, offset=offset)
        return data.get("data", [])

    def all_accounts(self, limit: int = DEFAULT_LIMIT) -> tuple[list[dict], int]:
        """Fetch ALL accounts via pagination. Returns (items, total_count)."""
        return self._list_all("/v1/accounts", limit=limit)

    def get_account(self, account_id: str) -> dict:
        """Get a single account by ID."""
        return self._get_one(f"/v1/accounts/{account_id}")

    def count_accounts(self) -> int:
        """Get total account count (fast — fetches 1 item)."""
        return self._count("/v1/accounts")

    # ── Opportunities ────────────────────────────────────────────────────────

    def list_opportunities(self, limit: int = DEFAULT_LIMIT, offset: int = 0) -> list[dict]:
        """List opportunities (deals)."""
        data = self._get("/v1/opportunities", limit=limit, offset=offset)
        return data.get("data", [])

    def all_opportunities(self, limit: int = DEFAULT_LIMIT) -> tuple[list[dict], int]:
        """Fetch ALL opportunities via pagination. Returns (items, total_count)."""
        return self._list_all("/v1/opportunities", limit=limit)

    def get_opportunity(self, opp_id: str) -> dict:
        """Get a single opportunity by ID."""
        return self._get_one(f"/v1/opportunities/{opp_id}")

    def count_opportunities(self) -> int:
        """Get total opportunity count (fast — fetches 1 item)."""
        return self._count("/v1/opportunities")

    # ── Tasks ────────────────────────────────────────────────────────────────

    def list_tasks(self, limit: int = DEFAULT_LIMIT, offset: int = 0) -> list[dict]:
        """List tasks."""
        data = self._get("/v1/tasks", limit=limit, offset=offset)
        return data.get("data", [])

    def all_tasks(self, limit: int = DEFAULT_LIMIT) -> tuple[list[dict], int]:
        """Fetch ALL tasks via pagination. Returns (items, total_count)."""
        return self._list_all("/v1/tasks", limit=limit)

    def get_task(self, task_id: str) -> dict:
        """Get a single task by ID."""
        return self._get_one(f"/v1/tasks/{task_id}")

    def count_tasks(self) -> int:
        """Get total task count (fast — fetches 1 item)."""
        return self._count("/v1/tasks")

    # ── Emails ───────────────────────────────────────────────────────────────

    def list_emails(self, limit: int = DEFAULT_LIMIT, offset: int = 0) -> list[dict]:
        """List emails."""
        data = self._get("/v1/emails", limit=limit, offset=offset)
        return data.get("data", [])

    def all_emails(self, limit: int = DEFAULT_LIMIT) -> tuple[list[dict], int]:
        """Fetch ALL emails via pagination. Returns (items, total_count)."""
        return self._list_all("/v1/emails", limit=limit)

    def get_email(self, email_id: str) -> dict:
        """Get a single email by ID."""
        return self._get_one(f"/v1/emails/{email_id}")

    def count_emails(self) -> int:
        """Get total email count (fast — fetches 1 item)."""
        return self._count("/v1/emails")

    # ── Notes ────────────────────────────────────────────────────────────────

    def list_notes(self, limit: int = DEFAULT_LIMIT) -> list[dict]:
        """List notes."""
        data = self._get("/v1/notes", limit=limit)
        return data.get("data", [])


# ── Singleton ────────────────────────────────────────────────────────────────

_client: LightfieldClient | None = None


def get_client() -> LightfieldClient:
    """Get or create the shared Lightfield client instance."""
    global _client
    if _client is None:
        _client = LightfieldClient()
    return _client


# ── Helper: extract display name from a resource ─────────────────────────────

def resource_name(resource: dict) -> str:
    """Best-effort display name for any CRM resource."""
    fields = resource.get("fields", {})

    # Try $name (FULL_NAME type) or $website or $email
    for field_key in ("$name", "$website", "$email"):
        fv = fields.get(field_key, {})
        val = fv.get("value")
        if val:
            if isinstance(val, list):
                return str(val[0]) if val else "—"
            return str(val)

    # Fallback: first text field found
    for key, fv in fields.items():
        if fv.get("value") and isinstance(fv["value"], str):
            return fv["value"]

    return resource.get("id", "—")[:16]
