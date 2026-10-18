"""Shared helpers for cloud-provider skills (digitalocean, aws, atlanticnet).

Keep this package provider-agnostic. Provider-specific auth, request shapes,
metadata service URLs, and response parsing live in the skills themselves.
"""
from . import manifest
from .http import HTTPError, http_json
from .ttl import TAG_MARKER, parse_ttl, ttl_from_tag, ttl_tag
from .cloudinit import (
    load_preset,
    merge_cloud_init,
    render_user_data,
    systemd_oneshot_timer,
)

__all__ = [
    "HTTPError",
    "http_json",
    "TAG_MARKER",
    "parse_ttl",
    "ttl_tag",
    "ttl_from_tag",
    "load_preset",
    "merge_cloud_init",
    "render_user_data",
    "systemd_oneshot_timer",
    "manifest",
]
