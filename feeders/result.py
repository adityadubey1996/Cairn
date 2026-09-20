"""Backward-compatible feeder counts with an explicit partial-failure signal.

Existing callers can still unpack ``seen, written = run()``. Schedulers must
check ``result.complete`` before advancing a watermark: an item-level failure
is not a successfully reconciled source, even when other items were written.
"""
from __future__ import annotations


class SyncResult(tuple):
    def __new__(cls, seen: int, written: int, failures: list[dict] | None = None):
        result = super().__new__(cls, (seen, written))
        result.failures = list(failures or [])
        return result

    @property
    def seen(self) -> int:
        return self[0]

    @property
    def written(self) -> int:
        return self[1]

    @property
    def failed(self) -> int:
        return len(self.failures)

    @property
    def complete(self) -> bool:
        return not self.failures


def failure(source_id: str, name: str, error: Exception | str) -> dict:
    return {"id": source_id, "name": name, "error": str(error)[:500]}
