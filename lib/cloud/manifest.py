"""Local TTL manifest for providers without first-class tags (e.g., Atlantic.net).

Entries are stored as a JSON list at `$XDG_STATE_HOME/cloudlib/ttl-manifest.json`
(fallback: ~/.local/state/cloudlib/ttl-manifest.json). Override with
CLOUDLIB_TTL_MANIFEST env var.

Each entry: {provider, instance_id, name, ttl, created_at, metadata}.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _default_path():
    override = os.environ.get("CLOUDLIB_TTL_MANIFEST")
    if override:
        return Path(override)
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "cloudlib" / "ttl-manifest.json"


def _load(path):
    if not path.exists():
        return []
    text = path.read_text().strip()
    return json.loads(text) if text else []


def _save(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2))


def add(provider, instance_id, name, ttl_utc, metadata=None, path=None):
    path = path or _default_path()
    entries = _load(path)
    entries.append({
        "provider": provider,
        "instance_id": str(instance_id),
        "name": name,
        "ttl": ttl_utc.astimezone(timezone.utc).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata or {},
    })
    _save(path, entries)


def remove(provider, instance_id, path=None):
    path = path or _default_path()
    entries = [e for e in _load(path)
               if not (e["provider"] == provider
                       and e["instance_id"] == str(instance_id))]
    _save(path, entries)


def expired(provider=None, now=None, path=None):
    path = path or _default_path()
    now = now or datetime.now(timezone.utc)
    out = []
    for e in _load(path):
        if provider and e["provider"] != provider:
            continue
        dt = datetime.fromisoformat(e["ttl"])
        if dt <= now:
            out.append(e)
    return out


def list_entries(provider=None, path=None):
    path = path or _default_path()
    return [e for e in _load(path) if not provider or e["provider"] == provider]
