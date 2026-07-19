from __future__ import annotations

KINDS = {"subject", "chapter", "point"}
ACTIVE_IMPORT_STATUSES = {"queued", "running"}


class KnowledgeDeleteError(ValueError):
    pass


def normalize_name(value: str) -> str:
    return (value or "").strip().casefold()
