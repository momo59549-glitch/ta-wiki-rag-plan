"""Runtime environment helpers for a local Prefect Server."""
from __future__ import annotations

import os
from urllib.parse import urlparse


def ensure_local_prefect_no_proxy() -> bool:
    """Keep Python/httpx from routing loopback Prefect traffic through a proxy."""
    host = urlparse(os.environ.get("PREFECT_API_URL", "")).hostname
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return False
    additions = ("127.0.0.1", "localhost", "::1")
    for key in ("NO_PROXY", "no_proxy"):
        values = [item.strip() for item in os.environ.get(key, "").split(",") if item.strip()]
        for item in additions:
            if item not in values:
                values.append(item)
        os.environ[key] = ",".join(values)
    return True
