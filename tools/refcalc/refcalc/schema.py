"""Validates the reference responses against openapi/openapi.yaml (needs pyyaml and openapi-schema-validator).

The responses are validated before redaction: an ignored text may be an array in the schema (`notes`)."""
from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from openapi_schema_validator import OAS30Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT4

SCHEMAS = {
    "upload": "ImportResult",
    "dismiss_regular_payment": "AnalyticsBlockUpdate",
    "/v1/statements/config": "ImportConfig",
    "/v1/operations": "OperationsList",
    "/v1/categories": "CategoriesList",
    "/v1/analytics": "Analytics",
    "/v1/analytics/charts/monthly_chart": "AnalyticsChartPage",
    "/v1/analytics/periods": "AnalyticsPeriodsList",
    "change_category": "ChangeCategoryResponse",
}


class SchemaCheck:
    def __init__(self, openapi_path: str):
        with open(openapi_path, encoding="utf-8") as f:
            spec = yaml.safe_load(f)
        self.registry = Registry().with_resource("openapi.yaml", Resource.from_contents(spec, DRAFT4))

    def errors(self, kind: str, response) -> list:
        name = SCHEMAS[kind]
        if isinstance(response, dict) and set(response) == {"status", "error"}:
            name, response = "ErrorResponse", {"error": response["error"]}
        schema = {"$ref": f"openapi.yaml#/components/schemas/{name}"}
        errors = self._validate(schema, response, "")
        # Conditional context requirements mirror currency/has_data without inventing a fallback zone.
        if isinstance(response, dict) and name in ("AnalyticsBlockUpdate", "Analytics", "AnalyticsChartPage", "OperationsList", "AnalyticsPeriodsList", "ImportConfig", "ImportResult"):
            needs_zone = (name in ("AnalyticsBlockUpdate", "AnalyticsChartPage", "ImportConfig", "ImportResult") or
                          name == "Analytics" and response.get("has_data") or
                          name == "AnalyticsPeriodsList" and bool(response.get("periods")) or
                          name == "OperationsList" and bool(response.get("items") or response.get("summary") or response.get("range") or
                              any((response.get("state") or {}).get(k) for k in ("data_to", "last_operation_at", "last_uploaded_at"))))
            zone = response.get("time_zone")
            if needs_zone and not zone:
                errors.append("time_zone: required for the returned calendar/financial data")
            if zone is not None:
                try:
                    ZoneInfo(zone)
                except (ZoneInfoNotFoundError, ValueError, TypeError):
                    errors.append("time_zone: unknown IANA zone")
        # Block `data` is a free object in the envelope: check it against the schema of its primitive (FIN-157).
        if name in ("AnalyticsBlockUpdate", "Analytics", "AnalyticsChartPage") and isinstance(response, dict):
            from .coverage import block_data_schema
            blocks = (response.get("blocks") or []) if name in ("Analytics", "AnalyticsBlockUpdate") else (
                [response["block"]] if isinstance(response.get("block"), dict) else [])
            for i, b in enumerate(blocks):
                try:
                    data_type = block_data_schema(b)
                except ValueError as e:
                    errors.append(f"blocks/{i}: {e}")
                    continue
                if b.get("data") is not None:
                    data_schema = {"$ref": f"openapi.yaml#/components/schemas/{data_type}"}
                    errors += self._validate(data_schema, b["data"], f"blocks/{i}/data/")
        if name == 'AnalyticsBlockUpdate' and isinstance(response, dict):
            ids = [b.get('id') for b in response.get('blocks', []) if isinstance(b, dict)]
            if not all(isinstance(i, str) and i for i in ids) or len(ids) != len(set(i for i in ids if isinstance(i, str))):
                errors.append('blocks: missing or duplicate id')
            params = response.get('params', {})
            if params.get('period') != 'month':
                errors.append('params: month required')
        return errors

    def _validate(self, schema, instance, prefix: str) -> list:
        validator = OAS30Validator(schema, registry=self.registry, format_checker=OAS30Validator.FORMAT_CHECKER)
        return [f"{prefix}{'/'.join(map(str, e.absolute_path)) or '$'}: {e.message}"
                for e in validator.iter_errors(instance)]
