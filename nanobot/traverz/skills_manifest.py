"""
Skills manifest sync — fetches the canonical skill list from the Traverz
backend at startup (and refreshes it periodically) so the bot's tool surface
is always aligned with backend capabilities without code changes.

Backend endpoint: GET {TRAVERZ_BACKEND_URL}/api/bot/skills/

The fetched manifest is cached in a module-level singleton.  The dynamic
`traverz_api` dispatcher tool reads from this cache to validate skill_id
arguments, look up HTTP method/path templates, and enforce role
restrictions before issuing the call.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from loguru import logger

_BACKEND_URL = os.environ.get("TRAVERZ_BACKEND_URL", "https://api.traverz.ai")
_REFRESH_INTERVAL_SECONDS = int(os.environ.get("TRAVERZ_SKILLS_REFRESH_S", "300"))  # 5 min
_FETCH_TIMEOUT = 10


@dataclass
class SkillsManifest:
    version: str = ""
    skills_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    fetched_at: float = 0.0

    def get(self, skill_id: str) -> dict[str, Any] | None:
        return self.skills_by_id.get(skill_id)

    def is_stale(self) -> bool:
        return (time.time() - self.fetched_at) > _REFRESH_INTERVAL_SECONDS


_manifest = SkillsManifest()
_lock = asyncio.Lock()


async def _fetch_manifest() -> dict[str, Any] | None:
    url = f"{_BACKEND_URL}/api/bot/skills/"
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
            resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001 - log and degrade gracefully
        logger.warning(f"Failed to fetch Traverz skills manifest from {url}: {exc}")
        return None


async def refresh(force: bool = False) -> SkillsManifest:
    """Fetch the latest manifest if stale (or forced).  Idempotent under lock."""
    global _manifest
    if not force and not _manifest.is_stale() and _manifest.skills_by_id:
        return _manifest
    async with _lock:
        if not force and not _manifest.is_stale() and _manifest.skills_by_id:
            return _manifest
        data = await _fetch_manifest()
        if not data:
            return _manifest  # keep prior copy
        skills_list = data.get("skills") or []
        _manifest = SkillsManifest(
            version=data.get("version", ""),
            skills_by_id={s["id"]: s for s in skills_list if "id" in s},
            raw=data,
            fetched_at=time.time(),
        )
        logger.info(
            f"Loaded Traverz skills manifest v{_manifest.version} "
            f"with {len(_manifest.skills_by_id)} skills"
        )
        return _manifest


def get() -> SkillsManifest:
    """Return the current cached manifest (may be empty before first fetch)."""
    return _manifest


async def ensure_loaded() -> SkillsManifest:
    """Ensure the manifest is loaded at least once; returns current state."""
    if not _manifest.skills_by_id:
        await refresh(force=True)
    return _manifest
