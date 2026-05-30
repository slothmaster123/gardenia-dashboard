"""*arr REST API client — Sonarr, Radarr, Lidarr.

Pure Python with no Flask dependency. Each service gets its own ArrClient
instance configured with base URL and API key.

Example:
    from services.arr_client import ArrClient

    sonarr = ArrClient("http://localhost:8989/api/v3", api_key="...")
    episodes = sonarr.get_calendar(days=7)
    wanted = sonarr.get_wanted()
    queue = sonarr.get_queue()
    health = sonarr.get_system_status()
    disk = sonarr.get_disk_space()
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# ── Defaults (override via environment) ──────────────────────────────────────
DEFAULT_SONARR_URL = os.environ.get("SONARR_URL", "http://localhost:8989/api/v3")
DEFAULT_SONARR_KEY = os.environ.get("SONARR_API_KEY", "e82858f3d78a416a831f8ebf65f5c168")
DEFAULT_RADARR_URL = os.environ.get("RADARR_URL", "http://localhost:7878/api/v3")
DEFAULT_RADARR_KEY = os.environ.get("RADARR_API_KEY", "96218fb9a0454a59ae0902b5ed2aad18")
DEFAULT_LIDARR_URL = os.environ.get("LIDARR_URL", "http://localhost:8686/api/v1")
DEFAULT_LIDARR_KEY = os.environ.get("LIDARR_API_KEY", "5c832711612e4ebd8d930c5616b348b1")

REQUEST_TIMEOUT = int(os.environ.get("ARR_REQUEST_TIMEOUT", "10"))


@dataclass
class ArrHealth:
    """Snapshot of a single *arr service health."""

    name: str
    url: str
    reachable: bool = False
    version: str = ""
    error: str = ""


class ArrClient:
    """HTTP client for a single *arr REST API (Sonarr, Radarr, or Lidarr)."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url: str = base_url.rstrip("/")
        self.api_key: str = api_key
        self._session: Optional[requests.Session] = None

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(
                {
                    "X-Api-Key": self.api_key,
                    "Accept": "application/json",
                }
            )
        return self._session

    # ── Core API methods ─────────────────────────────────────────────────

    def get_calendar(
        self,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return calendar entries (episodes / movies) in the given date range.

        ``start`` and ``end`` should be ISO-8601 strings (e.g. ``2026-06-01``).
        When omitted the server returns its default window (usually today → +7d).
        """
        params: dict[str, str] = {}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        try:
            resp = self.session.get(
                f"{self.base_url}/calendar",
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("Calendar fetch failed for %s: %s", self.base_url, exc)
            return []

    def get_wanted(self, page: int = 1, page_size: int = 50) -> list[dict[str, Any]]:
        """Return wanted / missing items (episodes, movies, or albums)."""
        try:
            resp = self.session.get(
                f"{self.base_url}/wanted/missing",
                params={"page": page, "pageSize": page_size, "sortKey": "releaseDate", "sortDirection": "ascending"},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            # Sonarr / Radarr return {page, pageSize, totalRecords, records: [...]}
            # Lidarr returns an array directly
            if isinstance(data, list):
                return data
            return data.get("records", [])
        except requests.RequestException as exc:
            logger.warning("Wanted fetch failed for %s: %s", self.base_url, exc)
            return []

    def get_queue(self, page: int = 1, page_size: int = 50) -> list[dict[str, Any]]:
        """Return active download queue."""
        try:
            resp = self.session.get(
                f"{self.base_url}/queue",
                params={"page": page, "pageSize": page_size},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            return data.get("records", [])
        except requests.RequestException as exc:
            logger.warning("Queue fetch failed for %s: %s", self.base_url, exc)
            return []

    def get_disk_space(self) -> list[dict[str, Any]]:
        """Return disk space info (root folders)."""
        try:
            resp = self.session.get(
                f"{self.base_url}/diskspace",
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("Disk space fetch failed for %s: %s", self.base_url, exc)
            return []

    def get_system_status(self) -> dict[str, Any]:
        """Return system status (version, app name, etc.)."""
        try:
            resp = self.session.get(
                f"{self.base_url}/system/status",
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException:
            return {}

    def get_health(self) -> ArrHealth:
        """Return a health snapshot — lightweight check suitable for dashboards."""
        health = ArrHealth(name="unknown", url=self.base_url)
        try:
            status = self.get_system_status()
            if status:
                health.reachable = True
                health.name = status.get("appName", "unknown")
                health.version = status.get("version", "")
            else:
                health.error = "Empty response from system/status"
        except Exception as exc:
            health.error = str(exc)
        return health


# ── Module-level convenience instances ───────────────────────────────────────

sonarr = ArrClient(DEFAULT_SONARR_URL, DEFAULT_SONARR_KEY)
radarr = ArrClient(DEFAULT_RADARR_URL, DEFAULT_RADARR_KEY)
lidarr = ArrClient(DEFAULT_LIDARR_URL, DEFAULT_LIDARR_KEY)

SERVICES = {"sonarr": sonarr, "radarr": radarr, "lidarr": lidarr}


def get_combined_health() -> dict[str, Any]:
    """Return health status for all three services in one call."""
    results: dict[str, Any] = {}
    for name, client in SERVICES.items():
        h = client.get_health()
        results[name] = {
            "reachable": h.reachable,
            "version": h.version,
            "error": h.error if not h.reachable else None,
        }
    return results


def get_combined_disk() -> dict[str, Any]:
    """Return disk space aggregated across all services."""
    combined: dict[str, Any] = {"total_space": 0, "free_space": 0, "used_space": 0, "services": {}}
    for name, client in SERVICES.items():
        try:
            disks = client.get_disk_space()
            svc_total = sum(d.get("totalSpace", 0) for d in disks)
            svc_free = sum(d.get("freeSpace", 0) for d in disks)
            combined["total_space"] += svc_total
            combined["free_space"] += svc_free
            combined["used_space"] += svc_total - svc_free
            combined["services"][name] = {"total": svc_total, "free": svc_free}
        except Exception:
            combined["services"][name] = {"total": 0, "free": 0, "error": "unreachable"}
    return combined
