"""Validate the FIN-224 error examples and producer schema boundaries.

Client display/fallback behavior is implemented and tested in FIN-223; this suite validates the wire contract,
including old responses, open codes, malformed optional presentation and unchanged retry metadata.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

import yaml
from openapi_schema_validator import OAS30Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT4


ROOT = Path(__file__).resolve().parents[1]


class ErrorPresentationSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = yaml.safe_load((ROOT / "openapi/openapi.yaml").read_text(encoding="utf-8"))
        cls.registry = Registry().with_resource("openapi.yaml", Resource.from_contents(spec, DRAFT4))
        cls.examples = json.loads((ROOT / "fixtures/errors/presentation.json").read_text(encoding="utf-8"))

    def errors(self, name, value):
        schema = {"$ref": f"openapi.yaml#/components/schemas/{name}"}
        validator = OAS30Validator(schema, registry=self.registry, format_checker=OAS30Validator.FORMAT_CHECKER)
        return list(validator.iter_errors(value))

    def test_wire_examples(self):
        for example in self.examples:
            with self.subTest(example=example["name"]):
                self.assertEqual(self.errors(example["schema"], example["value"]), [])

    def test_text_is_bounded_in_unicode_code_points(self):
        for value in ["Я", "🪙" * 1000, "\u200b", "\u00a0Текст\u3000"]:
            with self.subTest(value=value[:20]):
                self.assertEqual(self.errors("UserMessage", value), [])
        for value in ["", "я" * 1001, "🪙" * 1001, None, 1, [], {}]:
            with self.subTest(value=repr(value)[:20]):
                self.assertTrue(self.errors("UserMessage", value))

    def test_nonblank_uses_the_documented_unicode_white_space_set(self):
        # Explicit code points avoid language-specific isspace/trim/regex shorthand differences.
        points = list(range(0x9, 0xE)) + [0x20, 0x85, 0xA0, 0x1680] + list(range(0x2000, 0x200B))
        points += [0x2028, 0x2029, 0x202F, 0x205F, 0x3000]
        for value in [chr(point) for point in points] + ["".join(map(chr, points))]:
            with self.subTest(value=repr(value)):
                self.assertTrue(self.errors("UserMessage", value))

    def test_severity_is_open(self):
        for value in ["info", "warning", "error", "future_level"]:
            with self.subTest(value=value):
                self.assertEqual(self.errors("ErrorSeverity", value), [])
        for value in [None, 1, [], {}]:
            with self.subTest(value=repr(value)):
                self.assertTrue(self.errors("ErrorSeverity", value))

    def test_malformed_presentation_is_local_to_optional_fields(self):
        # Producers must satisfy the schema. Tolerant consumers must retain code/retry when optional fields are
        # malformed; demonstrate that these payloads still satisfy the original schema after ignoring only those
        # fields, rather than accepting diagnostics as UI text or changing the original metadata.
        names = {"ErrorResponse", "SseError", "StreamError", "AnswerErrorEvent", "Message"}
        for example in self.examples:
            if example["schema"] not in names:
                continue
            source = example["value"]
            original = source.get("error", source)
            history = example["schema"] == "Message"
            text_field = "error_user_message" if history else "user_message"
            severity_field = "error_severity" if history else "severity"
            for field, invalid in [(text_field, " "), (text_field, None), (text_field, {}),
                                   (severity_field, []), (severity_field, None)]:
                with self.subTest(example=example["name"], field=field, invalid=invalid):
                    value = copy.deepcopy(source)
                    error = value.get("error", value)
                    error[field] = invalid
                    errors = self.errors(example["schema"], value)
                    self.assertTrue(errors)
                    self.assertTrue(all(list(e.absolute_path)[-1] == field for e in errors))
                    del error[field]
                    self.assertEqual(self.errors(example["schema"], value), [])
                    for key in ("code", "error_code", "retry_at", "retryable", "remaining_limit"):
                        self.assertEqual(error.get(key), original.get(key))


if __name__ == "__main__":
    unittest.main()
