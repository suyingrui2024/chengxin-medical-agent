"""Which model endpoint a session is bound to, and keeping its secret out.

A session is bound to one endpoint, and the binding has to survive a
restart: so it is identified by a hash of the endpoint and model name rather
than by the address itself, which would put a credential-bearing URL into a
workspace directory name.

``scrub`` is the other half of the same concern -- a secret that reached a
log line or an error message must not stay there.
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import urlsplit

from .errors import ExecutionError


def route_identity(settings):
    endpoint = settings["MEDAGENT_LLM_BASE_URL"].rstrip("/")
    try:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError()
    except ValueError:
        raise ExecutionError("模型服务地址无效") from None
    return (
        hashlib.sha256(
            json.dumps(
                [endpoint, settings["MEDAGENT_LLM_MODEL"]], ensure_ascii=False
            ).encode()
        ).hexdigest(),
        parsed.hostname,
    )

def scrub(text, secret):
    return text.replace(secret, "[REDACTED]") if secret else text


__all__ = [
    "route_identity",
    "scrub",
]
