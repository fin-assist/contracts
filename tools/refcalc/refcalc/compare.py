"""Comparison rules of the reference responses (fixtures/calc/README.md, «Правила сравнения»).

`redact` replaces what is not compared with IGNORED in an expected response; `compare` checks an actual response
against it. Both implementations (fake backend, Go backend) follow the same rules.
"""
from __future__ import annotations

import datetime as dt
import math
import re

IGNORED = "<ignored>"
# Opaque values and moments that depend on the implementation or on the wall clock.
OPAQUE_KEYS = {"id", "replaced_id", "upload_id", "payment_id", "selection", "calculated_at"}
# Texts the server writes for the client: shown, not compared (their numbers come in structured fields).
TEXT_KEYS = {"selection_name", "type_name", "feature_name", "text", "alt_text", "empty_text", "title", "caption", "label",
             "note", "description", "badge", "sublabel", "value_suffix", "more_label", "notes", "spoken",
             "note_details", "source_name"}
# Outside blocks these are data, not texts: an operation's description, a notice code's text is still a text.
DATA_TITLE_OUTSIDE_BLOCKS = {"title", "note", "description"}
RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
FLOAT_TOLERANCE = 1e-9


def redact(value, in_blocks: bool = False, parent_is_items: bool = False, key: str = None):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if v is None:
                out[k] = None
            elif k in OPAQUE_KEYS:
                # `id` of a block, a tile, a ranked item (category) and a category in 5.1 is a stable key, not opaque.
                out[k] = v if (k == "id" and (in_blocks or key == "categories")) else IGNORED
            elif k in TEXT_KEYS and (in_blocks or k not in DATA_TITLE_OUTSIDE_BLOCKS):
                keep = in_blocks and k == "title" and parent_is_items
                out[k] = v if keep else IGNORED
            else:
                out[k] = redact(v, in_blocks or k == "blocks", False, k)
        return out
    if isinstance(value, list):
        return [redact(v, in_blocks, key == "items", key) for v in value]
    return value


def _moment(s):
    if isinstance(s, str) and RFC3339.match(s):
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return None


def compare(expected, actual, path: str = "$") -> list:
    """Differences as `path: expected != actual`. Absent equals null; a block or tile without `status` is ready;
    moments are compared as instants; floats with a tolerance; arrays in order and of the same length. A field the
    reference does not have must be absent or null in the actual response, unless it is a text or an opaque id."""
    if expected == IGNORED:
        return []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected an object, got {actual!r}"]
        diffs = []
        for k in sorted(set(expected) | set(actual)):
            e, a = expected.get(k), actual.get(k)
            if k == "status" and e == "ready" and a is None:
                continue
            if e is None and a is None:
                continue
            if k not in expected and (k in TEXT_KEYS or k in OPAQUE_KEYS):
                continue  # a text or an id the reference does not have: not compared
            diffs += compare(e, a, f"{path}.{k}")
        return diffs
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return [f"{path}: expected an array, got {actual!r}"]
        if len(expected) != len(actual):
            return [f"{path}: expected {len(expected)} elements, got {len(actual)}"]
        diffs = []
        for i, (e, a) in enumerate(zip(expected, actual)):
            diffs += compare(e, a, f"{path}[{i}]")
        return diffs
    if isinstance(expected, float) or isinstance(actual, float):
        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)) and \
                math.isclose(expected, actual, rel_tol=0, abs_tol=FLOAT_TOLERANCE):
            return []
        return [f"{path}: {expected!r} != {actual!r}"]
    me, ma = _moment(expected), _moment(actual)
    if me is not None and ma is not None:
        return [] if me == ma else [f"{path}: {expected} != {actual}"]
    return [] if expected == actual else [f"{path}: {expected!r} != {actual!r}"]
