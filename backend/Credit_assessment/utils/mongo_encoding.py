"""
Mongo-safe encoding
===================
Converts arbitrary pipeline output into values PyMongo can store, without
silently changing their meaning.

WHY THIS EXISTS
───────────────
Before this module there was no serialization layer at all — documents went to
``insert_one`` as raw Python dicts. Three failure modes followed from that:

1. **numpy types.** The pipeline avoided them by convention (wrapping reductions
   in ``float(...)``), not by construction. A single unwrapped ``np.float64``
   or ``np.bool_`` anywhere in the payload raises ``InvalidDocument`` and the
   whole assessment fails to save.

2. **NaN / Inf.** ``round(float('nan'), 4)`` is ``nan``, which stores as BSON
   NaN. That breaks ``$avg``/``$sum`` aggregations and does not survive strict
   JSON round-tripping. The satellite collector deliberately fills missing bins
   with ``np.nan``, so this was reaching the database routinely.

3. **Naive vs aware datetimes.** One write path used ``datetime.utcnow()``
   (naive), another ``datetime.now(timezone.utc)`` (aware), and several date
   fields were ISO strings — so documents could not be reliably sorted or range
   queried against each other.

DESIGN RULES
────────────
* **Absent is not zero.** NaN and Inf become ``None``, never ``0.0``. A missing
  observation must stay missing.
* **Lossless where possible.** Numbers are not rounded here; rounding is a
  presentation concern and belongs in the schema builder.
* **Fail loudly on the unencodable.** Unknown object types raise rather than
  being coerced to ``str(obj)``, which would produce a plausible-looking
  document containing garbage.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

try:
    import numpy as np
    _NUMPY = True
except ImportError:  # numpy is a hard dep in practice, but do not require it here
    np = None  # type: ignore
    _NUMPY = False


__all__ = ["to_mongo", "utc_now", "as_utc", "count_nulls"]


def utc_now() -> datetime:
    """Timezone-aware UTC now. Use this everywhere instead of datetime.utcnow()."""
    return datetime.now(timezone.utc)


def as_utc(value: Any) -> Any:
    """
    Coerce a datetime/date/ISO-string to a timezone-aware UTC datetime.

    Naive datetimes are *assumed* to be UTC — that matches what the pipeline
    meant by ``utcnow()``. Returns None for unparseable input rather than
    inventing a timestamp.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        # Python < 3.11 cannot parse a trailing 'Z'.
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _encode_float(value: float) -> Any:
    """
    Non-finite floats become None — a missing value, not a zero.

    Returns a builtin float, never a subclass. This matters: ``np.float64`` IS a
    subclass of ``float``, so it satisfies ``isinstance(v, float)`` and would
    otherwise pass through the primitive branch unconverted and reach PyMongo
    as a numpy type.
    """
    if math.isnan(value) or math.isinf(value):
        return None
    return float(value)


def to_mongo(value: Any, _depth: int = 0) -> Any:
    """
    Recursively convert ``value`` into BSON-encodable Python types.

    Handles: dict, list/tuple/set, numpy scalars and arrays, Decimal, date /
    datetime (-> aware UTC), NaN/Inf (-> None), and the JSON primitives.

    Raises TypeError on anything else, deliberately. Coercing an unknown object
    to its ``repr`` would store a plausible-looking string that no consumer can
    interpret — worse than failing the write.
    """
    if _depth > 64:
        raise ValueError("to_mongo: maximum nesting depth exceeded (cyclic structure?)")

    # ── Primitives ────────────────────────────────────────────────────────
    if value is None or isinstance(value, (str, bytes)):
        return value
    # bool must precede int — bool is a subclass of int.
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return _encode_float(value)

    # ── Dates ─────────────────────────────────────────────────────────────
    if isinstance(value, (datetime, date)):
        return as_utc(value)

    # ── numpy ─────────────────────────────────────────────────────────────
    if _NUMPY:
        if isinstance(value, np.bool_):
            return bool(value)
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return _encode_float(float(value))
        if isinstance(value, np.datetime64):
            return as_utc(value.astype("datetime64[ms]").astype(datetime))
        if isinstance(value, np.ndarray):
            return [to_mongo(v, _depth + 1) for v in value.tolist()]
        if isinstance(value, np.generic):
            return to_mongo(value.item(), _depth + 1)

    # ── Containers ────────────────────────────────────────────────────────
    if isinstance(value, dict):
        # BSON keys must be strings and may not contain '.' or start with '$'.
        out = {}
        for k, v in value.items():
            key = k if isinstance(k, str) else str(k)
            if key.startswith("$"):
                key = "_" + key[1:]
            if "." in key:
                key = key.replace(".", "_")
            out[key] = to_mongo(v, _depth + 1)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_mongo(v, _depth + 1) for v in value]

    # ── Misc ──────────────────────────────────────────────────────────────
    if isinstance(value, Decimal):
        return _encode_float(float(value))

    raise TypeError(
        f"to_mongo: unsupported type {type(value).__name__!r} at depth {_depth}. "
        f"Convert it explicitly rather than letting it be stringified."
    )


def count_nulls(value: Any) -> int:
    """
    Count None values in an encoded structure.

    Useful as a data-quality signal: a series that encoded mostly to nulls means
    the underlying observations were missing, and callers should record that
    rather than presenting the series as if it were complete.
    """
    if value is None:
        return 1
    if isinstance(value, dict):
        return sum(count_nulls(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return sum(count_nulls(v) for v in value)
    return 0
