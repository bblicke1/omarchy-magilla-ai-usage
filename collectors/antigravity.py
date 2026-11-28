"""Antigravity (Google AI Pro/Ultra) subscription quota for the Gemini slot.

agy is signed in on the Hermes VM, not on this machine, so the read-only
quota call runs there (`ssh hermes ~/.local/bin/agy-quota`) and only the
fractions come back. No token ever touches this machine. Local patch
(magilla-ai-usage.patch); not upstream.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

import lib

AGENT_ID = "gemini"
AGENT_NAME = "Antigravity"
SSH_HOST = os.environ.get("MAGILLA_AGY_HOST", "hermes")
REMOTE = "~/.local/bin/agy-quota"
PROBE_MIN_INTERVAL_SECONDS = 120
AUTH_HELP = "agy on the Hermes VM is not signed in. Run `agy` there to sign in."
WINDOW_TITLE = {"5h": "5-hour", "weekly": "Weekly"}


def _fetch() -> dict[str, Any] | None:
  try:
    proc = subprocess.run(
      ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", SSH_HOST, REMOTE],
      capture_output=True, text=True, timeout=60,
    )
  except (OSError, subprocess.TimeoutExpired):
    return None
  try:
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
  except (IndexError, json.JSONDecodeError):
    return None
  return payload if isinstance(payload, dict) else None


def _limits(payload: dict[str, Any]) -> list[dict[str, Any]]:
  limits: list[dict[str, Any]] = []
  for group in payload.get("groups") or []:
    name = str(group.get("name") or "")
    gemini = "gemini" in name.lower()
    prefix = "" if gemini else "Claude/GPT "
    for bucket in group.get("buckets") or []:
      remaining = bucket.get("remainingFraction")
      try:
        used = min(1.0, max(0.0, 1.0 - float(remaining)))
      except (TypeError, ValueError):
        continue
      window = str(bucket.get("window") or "")
      title = prefix + WINDOW_TITLE.get(window, window or "Usage")
      entry = {
        "label": f"{title} limit",
        "title": title,
        "percent": used,
        # An untouched window reports now+window as its reset; hide that.
        "resetsAt": str(bucket.get("resetTime") or "") if used > 0 else "",
      }
      if not gemini:
        # Separate pool; shown in the panel but never drives the chip.
        entry["headline"] = False
      limits.append(entry)
  # Chip follows the tighter Gemini window.
  gem = [l for l in limits if "headline" not in l]
  if gem:
    max(gem, key=lambda l: l["percent"])["headline"] = True
  return limits


def collect(force: bool = False, limits_only: bool = False) -> dict[str, Any]:
  del limits_only
  cache_path = lib.magilla_cache_dir() / "antigravity-limits.json"
  cached = lib.read_json(cache_path) or {}
  record = lib.base_record(AGENT_ID, AGENT_NAME, scope="account")
  fetched_at = lib.number(cached.get("fetchedAtMs")) / 1000
  if cached.get("limits") and not force and lib.time_now() - fetched_at < PROBE_MIN_INTERVAL_SECONDS:
    record.update({"ready": True, "limits": cached["limits"], "tierLabel": cached.get("tierLabel", "")})
    return record

  payload = _fetch()
  if payload and payload.get("ok"):
    snapshot = {
      "fetchedAtMs": round(lib.time_now() * 1000),
      "limits": _limits(payload),
      "tierLabel": lib.safe_display_text(str(payload.get("tier") or "")),
    }
    try:
      lib.write_json(cache_path, snapshot)
    except OSError:
      pass
    record.update({"ready": True, "limits": snapshot["limits"], "tierLabel": snapshot["tierLabel"]})
    return record

  record.update({
    "limits": cached.get("limits") or [],
    "tierLabel": cached.get("tierLabel", ""),
    "ready": bool(cached.get("limits")),
    "retryAdvised": True,
    "usageStatusText": "Antigravity limits unavailable",
    "authHelpText": AUTH_HELP if payload and "401" in str(payload.get("error")) else
                    f"Couldn't reach {SSH_HOST} for Antigravity quota. Retrying shortly.",
  })
  return record
