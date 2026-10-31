"""Schema coverage of the reference responses (FIN-157): every field of the response schemas the fixtures check has a
value in at least one expected response, unless fixtures/calc/coverage-exceptions.json says why it cannot.

A field is `Owner.property`: `Owner` is the component schema that declares it (`Analytics.period`), or the path of an
inline object inside one (`Analytics.period.gaps[]`). Block `data` is walked with the schema of its primitive
(`ChartData` for `chart`), only for the primitives that occur in the responses.
"""
from __future__ import annotations

import yaml

# Primitive of a block → schema of its `data` (openapi.yaml, `Block`).
BLOCK_DATA = {
    "heading": "HeadingData", "text": "TextData", "rows": "RowsData", "chart": "ChartData",
    "tile_group": "TileGroupData", "ranked_list": "RankedListData", "insight_card": "InsightCardData",
}


def block_data_schema(block: dict):
    """Select the exact known primitive version; never validate a new shape as an older one."""
    if block.get("type") == "tile_group" and block.get("v", 1) == 2:
        return "TileGroupDataV2"
    if block.get("v", 1) == 1 and block.get("type") in BLOCK_DATA:
        return BLOCK_DATA[block["type"]]
    raise ValueError(f"unsupported expected block {block.get('type')}@{block.get('v', 1)}")


class Coverage:
    def __init__(self, openapi_path: str):
        with open(openapi_path, encoding="utf-8") as f:
            self.schemas = yaml.safe_load(f)["components"]["schemas"]
        self.universe = set()
        self.observed = set()
        self.data_types = set()

    @staticmethod
    def _supported(schema, owner) -> None:
        """The walk follows $ref, allOf, properties and items; anything else would hide fields silently."""
        bad = [k for k in ("oneOf", "anyOf", "not") if k in schema]
        if isinstance(schema.get("additionalProperties"), dict):
            bad.append("additionalProperties")
        if bad:
            raise ValueError(f"{owner}: {', '.join(bad)} is not supported by the coverage check — extend coverage.py")

    def _deref(self, schema, owner):
        if "$ref" in schema:
            name = schema["$ref"].rsplit("/", 1)[-1]
            return self.schemas[name], name
        return schema, owner

    # ---- static: every field reachable from the roots ----

    def collect(self, root: str) -> None:
        self._collect({"$ref": f"#/components/schemas/{root}"}, root, set())

    def _collect(self, schema, owner, seen) -> None:
        schema, owner = self._deref(schema, owner)
        self._supported(schema, owner)
        key = (owner, id(schema))
        if key in seen:
            return
        seen.add(key)
        for sub in schema.get("allOf", []):
            self._collect(sub, owner, seen)
        for prop, sub in schema.get("properties", {}).items():
            self.universe.add(f"{owner}.{prop}")
            self._collect(sub, f"{owner}.{prop}", seen)
        if "items" in schema:
            self._collect(schema["items"], f"{owner}[]", seen)
        if owner == "Block":
            for name in sorted(self.data_types):
                self._collect({"$ref": f"#/components/schemas/{name}"}, name, seen)

    # ---- dynamic: fields with a value in the responses ----

    def observe(self, root: str, instance) -> None:
        self._observe({"$ref": f"#/components/schemas/{root}"}, root, instance)

    def _observe(self, schema, owner, instance) -> None:
        if instance is None:
            return
        schema, owner = self._deref(schema, owner)
        for sub in schema.get("allOf", []):
            self._observe(sub, owner, instance)
        if isinstance(instance, dict):
            for prop, sub in schema.get("properties", {}).items():
                if instance.get(prop) is not None:
                    self.observed.add(f"{owner}.{prop}")
                    self._observe(sub, f"{owner}.{prop}", instance[prop])
            if owner == "Block":
                name = block_data_schema(instance)
                self.data_types.add(name)
                if instance.get("data") is not None:
                    self._observe({"$ref": f"#/components/schemas/{name}"}, name, instance["data"])
        elif isinstance(instance, list) and "items" in schema:
            for el in instance:
                self._observe(schema["items"], f"{owner}[]", el)

    def report(self, roots: set, exceptions: dict) -> list:
        """Errors: uncovered fields without an exception, and exceptions that are stale."""
        for r in sorted(roots):
            self.collect(r)
        errors = []
        for f in sorted(self.universe - self.observed - set(exceptions)):
            errors.append(f"not covered by any expected response: {f}")
        for f in sorted(set(exceptions)):
            if f not in self.universe:
                errors.append(f"coverage exception for an unknown field: {f}")
            elif f in self.observed:
                errors.append(f"coverage exception for a covered field (remove it): {f}")
        return errors
