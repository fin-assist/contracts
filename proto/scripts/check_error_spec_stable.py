#!/usr/bin/env python3
"""Check that (error_spec) of existing registry codes did not change against the PR base.

Usage:
    buf build '<repo>#format=git,commit=<base>,subdir=proto' -o base.binpb
    buf build -o head.binpb
    python3 scripts/check_error_spec_stable.py base.binpb head.binpb [--accepted]

A code present in both versions must keep its error_spec (grpc_code, http_status,
metadata_keys, details): buf breaking does not compare custom options, and a changed
spec changes the finerr constructor and turns the error into a 500 while services and
bank-bff run different module versions (docs/grpc-api.md 5.1, rule 5). New and removed
codes are not checked here (removal goes through `reserved`, buf breaking).

--accepted: the owner accepted the change (PR label `breaking-accepted`); changes are
listed but the check passes.

Exit code 0 when nothing changed or the change is accepted, 1 otherwise.
"""

import sys

from check_error_registry import REGISTRY, SPEC_EXT, load_pool, options_with_ext


def specs(path):
    """{code: error_spec as a comparable tuple} of the registry in a descriptor set."""
    _, pool = load_pool(path)
    ext = pool.FindExtensionByName(SPEC_EXT)
    enum = pool.FindEnumTypeByName(REGISTRY)
    out = {}
    for value in enum.values:
        if value.number == 0:
            continue
        opts = options_with_ext(pool, value.GetOptions())
        if not opts.HasExtension(ext):
            out[value.name] = None
            continue
        spec = opts.Extensions[ext]
        out[value.name] = (
            spec.grpc_code,
            spec.http_status,
            tuple(spec.metadata_keys),
            tuple(sorted(spec.details)),
        )
    return out


FIELDS = ("grpc_code", "http_status", "metadata_keys", "details")


def main(base_path, head_path, accepted):
    base, head = specs(base_path), specs(head_path)
    changes = []
    for name in sorted(base.keys() & head.keys()):
        if base[name] == head[name]:
            continue
        if base[name] is None or head[name] is None:
            changes.append(f"{name}: error_spec {'added' if base[name] is None else 'removed'}")
            continue
        for field, old, new in zip(FIELDS, base[name], head[name]):
            if old != new:
                changes.append(f"{name}.{field}: {old!r} -> {new!r}")
    if not changes:
        print(f"error_spec stable: {len(base.keys() & head.keys())} existing codes unchanged")
        return 0
    print("error_spec of existing codes changed (docs/grpc-api.md 5.1, rule 5):")
    for c in changes:
        print(f"  - {c}")
    if accepted:
        print("accepted by the owner (label breaking-accepted): passing")
        return 0
    print("a new client reaction needs a new code; to accept this change the owner adds the "
          "PR label breaking-accepted")
    return 1


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--accepted"]
    if len(args) != 2:
        raise SystemExit(__doc__)
    sys.exit(main(args[0], args[1], "--accepted" in sys.argv[1:]))
