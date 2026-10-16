"""Validates the reference responses against openapi/openapi.yaml (needs pyyaml and openapi-schema-validator).

The responses are validated before redaction: an ignored text may be an array in the schema (`notes`)."""
from __future__ import annotations

import yaml
from openapi_schema_validator import OAS30Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT4

SCHEMAS = {
    "upload": "ImportResult",
    "/v1/operations": "OperationsList",
    "/v1/categories": "CategoriesList",
    "/v1/analytics": "Analytics",
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
        validator = OAS30Validator(schema, registry=self.registry, format_checker=OAS30Validator.FORMAT_CHECKER)
        return [f"{'/'.join(map(str, e.absolute_path)) or '$'}: {e.message}" for e in validator.iter_errors(response)]
