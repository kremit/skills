"""Small JSON-over-HTTP helper with a clean error type.

Caller supplies headers (including auth) so this stays provider-agnostic.
"""
import json
import urllib.error
import urllib.parse
import urllib.request


class HTTPError(Exception):
    def __init__(self, code, reason, body, headers):
        self.code = code
        self.reason = reason
        self.body = body
        self.headers = dict(headers) if headers else {}
        super().__init__(f"{code} {reason}: {body[:500]}")


def http_json(method, url, *, headers=None, body=None, query=None, timeout=30):
    """Send a JSON request and return parsed JSON (or {} on empty 2xx).

    Raises HTTPError for any non-2xx response.
    """
    if query:
        url = url + "?" + urllib.parse.urlencode(query, doseq=True)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None and not (headers and "Content-Type" in headers):
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        raise HTTPError(e.code, e.reason, body_text, e.headers) from None
