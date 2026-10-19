"""TTL parsing and tag formatting shared across cloud-provider skills.

Tag format is deliberately constrained to characters accepted by every
provider we target (DO, AWS, Atlantic.net): alphanumerics and hyphens,
uppercase T/Z in timestamps.
"""
import re
from datetime import datetime, timedelta, timezone

TAG_MARKER = "auto-ttl"
_DURATION_RE = re.compile(r"^(\d+)([mhd])$")
_TAG_RE = re.compile(r"^ttl-(\d{8}T\d{6}Z)$")


def parse_ttl(spec):
    """Parse "4h" | "30m" | "2d" | ISO8601 into an aware UTC datetime.

    Raises ValueError on invalid input.
    """
    m = _DURATION_RE.match(spec)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"m": timedelta(minutes=n),
                 "h": timedelta(hours=n),
                 "d": timedelta(days=n)}[unit]
        return datetime.now(timezone.utc) + delta
    try:
        dt = datetime.fromisoformat(spec.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"Invalid TTL {spec!r}; use 4h / 30m / 2d or ISO8601") from e
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ttl_tag(dt):
    """Format an aware UTC datetime as a tag-safe timestamp."""
    return "ttl-" + dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ttl_from_tag(tag):
    """Parse a ttl-YYYYMMDDTHHMMSSZ tag back to an aware UTC datetime, or None."""
    m = _TAG_RE.match(tag)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
