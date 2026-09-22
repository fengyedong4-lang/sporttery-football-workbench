from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


OFFICIAL_ROOTS = ("sporttery.cn", "lottery.gov.cn")


def is_official_hostname(url: str) -> bool:
    host = (urlparse(url).hostname or "").rstrip(".").lower()
    return any(host == root or host.endswith("." + root) for root in OFFICIAL_ROOTS)


def validate_redirect_chain(urls: list[str]) -> bool:
    return bool(urls) and all(is_official_hostname(url) for url in urls)


def safe_child(base: Path, user_path: str) -> Path:
    base = base.resolve()
    candidate = (base / user_path).resolve()
    if candidate != base and base not in candidate.parents:
        raise ValueError("路径越界")
    return candidate

