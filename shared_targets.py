"""Read owner-approved dynamic targets shared by all Telegram publishers."""

import json
import os
import time
import urllib.error
import urllib.request


_cache = {"at": 0.0, "groups": set()}


def normalize_group(value):
    text = str(value or "").strip().lower().lstrip("@").rstrip("/")
    if "t.me/" in text:
        text = text.split("t.me/", 1)[1].split("?", 1)[0].strip("/")
    return text


def _get_json(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.HTTPError):
        return None


def shared_approved_groups(force=False):
    if not force and time.monotonic() - _cache["at"] < 60:
        return set(_cache["groups"])
    project = os.environ.get("FIREBASE_PROJECT_ID", "bot-2-63772").strip()
    api_key = os.environ.get("FIREBASE_API_KEY", "").strip()
    groups = set()
    if api_key:
        base = (
            f"https://firestore.googleapis.com/v1/projects/{project}/"
            "databases/(default)/documents"
        )
        registry = _get_json(f"{base}/reklam/target_registry?key={api_key}") or {}
        raw = registry.get("fields", {}).get("registry_json", {}).get("stringValue", "")
        try:
            candidates = json.loads(raw or "{}").get("candidates", {})
        except ValueError:
            candidates = {}
        for username, row in candidates.items():
            if isinstance(row, dict) and row.get("status") == "approved" and row.get("active", True):
                groups.add(normalize_group(username))

        # Backward-compatible bridge for approvals created before the registry.
        state = _get_json(f"{base}/reklam/state?key={api_key}") or {}
        legacy = state.get("fields", {}).get("auto_groups_list", {}).get("stringValue", "")
        groups.update(normalize_group(item) for item in legacy.splitlines() if normalize_group(item))
    env_groups = os.environ.get("SMM_SHARED_APPROVED_GROUPS", "")
    groups.update(normalize_group(item) for item in env_groups.split(",") if normalize_group(item))
    _cache.update(at=time.monotonic(), groups=groups)
    return set(groups)
