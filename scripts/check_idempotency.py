"""Enforce the POST idempotency declarations in the local OpenAPI document (FIN-77)."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import yaml


KEY_REF = "#/components/parameters/IdempotencyKey"
ERROR_CODES = {"IDEMPOTENCY_CONFLICT", "REQUEST_IN_PROGRESS"}


class InvalidSpec(ValueError):
    pass


def resolve(spec: dict, value: object) -> dict:
    """Resolve local Reference Objects only; never fetch files or URLs from a PR."""
    seen = set()
    while isinstance(value, dict) and "$ref" in value:
        ref = value["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
            raise InvalidSpec(f"unsupported or cyclic reference: {ref!r}")
        seen.add(ref)
        value = spec
        try:
            for part in ref[2:].split("/"):
                value = value[part.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError):
            raise InvalidSpec(f"unresolved reference: {ref}") from None
    if not isinstance(value, dict):
        raise InvalidSpec("expected an OpenAPI object")
    return value


def parameters(spec: dict, item: dict) -> dict:
    result = {}
    values = item.get("parameters", [])
    if not isinstance(values, list):
        raise InvalidSpec("parameters must be an array")
    for raw in values:
        param = resolve(spec, raw)
        name, location = param["name"], param["in"]
        # HTTP header names are case-insensitive, including operation overrides.
        identity = (location, name.lower() if location == "header" else name)
        if identity in result:
            raise InvalidSpec(f"duplicate parameter: {name}")
        result[identity] = raw
    return result


def check_conflict(spec: dict, operation: dict) -> None:
    response = resolve(spec, operation.get("responses", {}).get("409"))
    headers = response.get("headers", {})
    retry_headers = [value for name, value in headers.items() if name.lower() == "retry-after"]
    if len(retry_headers) != 1:
        raise InvalidSpec("409 must declare Retry-After")
    schema = resolve(spec, resolve(spec, retry_headers[0]).get("schema"))
    if schema.get("type") != "integer" or schema.get("minimum") != 0 or schema.get("nullable", False):
        raise InvalidSpec("409 Retry-After must be a non-null integer with minimum: 0 (seconds)")
    media = response.get("content", {}).get("application/json", {})
    examples = media.get("examples", {})
    codes = set()
    for example in examples.values():
        value = resolve(spec, example).get("value", {})
        code = value.get("error", {}).get("code")
        if isinstance(code, str):
            codes.add(code)
    if not ERROR_CODES <= codes:
        raise InvalidSpec("409 application/json examples must include IDEMPOTENCY_CONFLICT and REQUEST_IN_PROGRESS")


def check_spec(spec: object) -> list[str]:
    errors = []
    try:
        spec = resolve({}, spec)
        key = resolve(spec, {"$ref": KEY_REF})
        schema = resolve(spec, key.get("schema"))
        if not (key.get("name") == "Idempotency-Key" and key.get("in") == "header"
                and key.get("required") is True and schema.get("type") == "string"
                and schema.get("format") == "uuid" and not schema.get("nullable", False)):
            raise InvalidSpec("IdempotencyKey must be a required, non-null UUID header")
        paths = spec["paths"]
        if not isinstance(paths, dict):
            raise InvalidSpec("paths must be an object")
    except (InvalidSpec, KeyError, TypeError, AttributeError) as exc:
        return [f"spec: {exc}"]

    for path, raw_item in paths.items():
        if path.startswith("x-"):
            continue
        try:
            item = resolve(spec, raw_item)
            if "post" not in item:
                continue
            operation = resolve(spec, item["post"])
            effective = parameters(spec, item) | parameters(spec, operation)
            key = effective.get(("header", "idempotency-key"))
            if "x-idempotency" in operation:
                reason = operation.get("x-idempotency-reason")
                if operation["x-idempotency"] != "not-required" or not isinstance(reason, str) or not reason.strip():
                    raise InvalidSpec("exception needs x-idempotency: not-required and nonblank x-idempotency-reason")
                if key is not None:
                    raise InvalidSpec("exception must not also declare Idempotency-Key")
            else:
                if not isinstance(key, dict) or key.get("$ref") != KEY_REF:
                    raise InvalidSpec(f"POST must reference {KEY_REF} or declare an explicit exception with a reason")
                check_conflict(spec, operation)
        except (InvalidSpec, KeyError, TypeError, AttributeError) as exc:
            errors.append(f"POST {path}: {exc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    args = parser.parse_args()
    try:
        errors = check_spec(yaml.safe_load(args.spec.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        errors = [f"cannot read OpenAPI: {exc}"]
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("POST idempotency declarations are valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
