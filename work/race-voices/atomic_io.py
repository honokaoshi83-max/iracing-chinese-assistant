"""Shared helpers for the small status, owner and journal files used across services.

Readers in other processes must never see a half-written JSON file, so every
write goes to a sibling temporary file first and is then renamed into place.
"""
import json
from pathlib import Path


def write_json(path, value, suffix='.tmp', **dumps):
    """Atomically replace ``path`` with ``value`` serialized as JSON.

    ``suffix`` names the sibling temporary file (``path.with_suffix(suffix)``);
    ``dumps`` is passed to ``json.dumps`` so each caller keeps its exact format.
    """
    path = Path(path)
    temporary = path.with_suffix(suffix)
    temporary.write_text(json.dumps(value, **dumps), encoding='utf-8')
    temporary.replace(path)


def append_jsonl(path, row):
    """Append one UTF-8 JSON line to a diagnostic journal."""
    with Path(path).open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + '\n')


def release_if_owned(path, owner, key=None):
    """Delete ``path`` only while it still records ``owner``.

    With ``key`` the stored field ``row[key]`` is compared, otherwise the whole
    stored record. A newer owner's file is left alone; read errors are ignored.
    """
    path = Path(path)
    try:
        row = json.loads(path.read_text(encoding='utf-8'))
        if (row.get(key) if key is not None else row) == owner:
            path.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError, AttributeError):
        pass
