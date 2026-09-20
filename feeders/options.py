"""Validate per-connection scope before any remote fetch begins."""
from urllib.parse import urlsplit
import re


def max_items(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("max_items must be a non-negative integer; 0 means no item limit")
    return value


def source_ids(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or any(
            not isinstance(item, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", item) for item in value):
        raise ValueError("source_ids must be a list of Google file or folder IDs")
    return list(dict.fromkeys(value))


def urls(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("urls must be a list of public http or https URLs")
    out = []
    for item in value:
        parsed = urlsplit(item)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("urls must contain public http or https URLs without embedded credentials")
        out.append(item)
    return list(dict.fromkeys(out))
