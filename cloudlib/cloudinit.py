"""Cloud-init preset loading, merging, and systemd-timer helpers.

Presets are standard cloud-config YAML without the "#cloud-config" header.
One skill-only key is recognized: `x-extends: <preset-name>` for inheritance.
List-valued keys (packages, runcmd, write_files) concatenate; dict-valued
keys merge recursively; scalars overwrite.

Provider-specific self-destruct scripts are supplied by the caller;
`systemd_oneshot_timer` only builds the generic write_files + runcmd scaffolding.
"""
from pathlib import Path

import yaml


def load_preset(presets_dir, name, _seen=None):
    """Load <presets_dir>/<name>.yaml, recursively applying `x-extends`."""
    seen = _seen or set()
    if name in seen:
        raise ValueError(f"Preset inheritance cycle through {name!r}")
    seen.add(name)
    path = Path(presets_dir) / f"{name}.yaml"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in Path(presets_dir).glob("*.yaml")))
        raise FileNotFoundError(
            f"Preset {name!r} not found. Available: {available or '(none)'}")
    cfg = yaml.safe_load(path.read_text()) or {}
    parent = cfg.pop("x-extends", None)
    if parent:
        return merge_cloud_init(load_preset(presets_dir, parent, seen), cfg)
    return cfg


def merge_cloud_init(base, extra):
    """Merge two cloud-config dicts: lists concat, dicts recurse, scalars overwrite."""
    out = dict(base)
    for k, v in extra.items():
        if k in out and isinstance(out[k], list) and isinstance(v, list):
            out[k] = out[k] + v
        elif k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = merge_cloud_init(out[k], v)
        else:
            out[k] = v
    return out


def render_user_data(cfg):
    """Render a cloud-config dict as a #cloud-config document (or empty string)."""
    if not cfg:
        return ""
    return "#cloud-config\n" + yaml.safe_dump(cfg, sort_keys=False)


def systemd_oneshot_timer(name, when_utc, script_path, script_body):
    """Build write_files + runcmd entries for a one-shot systemd timer.

    Returns (write_files_list, runcmd_list). Caller appends to their cloud-config.
    The provider supplies `script_body` (shell) since the actual self-destruct
    call differs per cloud (DO API vs AWS EC2 terminate vs Atlantic API).
    """
    when_str = when_utc.strftime("%Y-%m-%d %H:%M:%S")
    files = [
        {
            "path": script_path,
            "permissions": "0750",
            "owner": "root:root",
            "content": script_body,
        },
        {
            "path": f"/etc/systemd/system/{name}.service",
            "content": (
                f"[Unit]\nDescription={name} oneshot\n\n"
                "[Service]\nType=oneshot\n"
                f"ExecStart={script_path}\n"
            ),
        },
        {
            "path": f"/etc/systemd/system/{name}.timer",
            "content": (
                f"[Unit]\nDescription={name} at TTL\n\n"
                f"[Timer]\nOnCalendar={when_str} UTC\n"
                f"Persistent=true\nUnit={name}.service\n\n"
                "[Install]\nWantedBy=timers.target\n"
            ),
        },
    ]
    runs = [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "--now", f"{name}.timer"],
    ]
    return files, runs
