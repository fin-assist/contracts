"""Exercise FIN-77 through contract mutations and the CI command's exit status."""
import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

from check_idempotency import check_spec


ROOT = Path(__file__).resolve().parents[1]
KEY = {"$ref": "#/components/parameters/IdempotencyKey"}


class IdempotencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = yaml.safe_load((ROOT / "openapi/openapi.yaml").read_text(encoding="utf-8"))

    def setUp(self):
        self.spec = copy.deepcopy(self.original)
        self.path = "/v1/auth/phone"
        self.post = self.spec["paths"][self.path]["post"]

    def assert_invalid(self, text):
        self.assertIn(text, "\n".join(check_spec(self.spec)))

    def test_repository_contract(self):
        self.assertEqual(check_spec(self.spec), [])

    def test_every_keyed_post_requires_key_and_conflict(self):
        original = copy.deepcopy(self.spec)
        for path, item in original["paths"].items():
            if KEY not in item.get("post", {}).get("parameters", []):
                continue
            with self.subTest(path=path, missing="key"):
                self.spec = copy.deepcopy(original)
                self.spec["paths"][path]["post"]["parameters"].remove(KEY)
                self.assert_invalid(f"POST {path}: POST must reference")
            with self.subTest(path=path, missing="409"):
                self.spec = copy.deepcopy(original)
                del self.spec["paths"][path]["post"]["responses"]["409"]
                self.assert_invalid(f"POST {path}:")

    def test_new_post_is_not_implicitly_exempt(self):
        self.spec["paths"]["/v1/new"] = {"post": {"responses": {"204": {"description": "OK"}}}}
        self.assert_invalid("POST /v1/new: POST must reference")

    def test_explicit_exception_needs_a_reason(self):
        post = self.spec["paths"]["/v1/auth/logout"]["post"]
        for reason in [None, "", " \n\t", 1, {}]:
            with self.subTest(reason=reason):
                post["x-idempotency-reason"] = reason
                self.assert_invalid("nonblank x-idempotency-reason")
        post["x-idempotency-reason"] = "Repeated logout returns 204."
        post["x-idempotency"] = "typo"
        self.assert_invalid("x-idempotency: not-required")

    def test_key_and_exception_are_mutually_exclusive(self):
        self.post.update({"x-idempotency": "not-required", "x-idempotency-reason": "Already safe."})
        self.assert_invalid("exception must not also declare")

    def test_shared_key_definition_is_required_nonnull_uuid_header(self):
        original = copy.deepcopy(self.spec)
        for field, invalid in [("required", False), ("required", "true"), ("in", "query"),
                               ("name", "Other-Key"), ("schema", {"type": "string"}),
                               ("schema", {"type": "integer", "format": "uuid"}),
                               ("schema", {"type": "string", "format": "uuid", "nullable": True})]:
            with self.subTest(field=field, invalid=invalid):
                self.spec = copy.deepcopy(original)
                self.spec["components"]["parameters"]["IdempotencyKey"][field] = invalid
                self.assert_invalid("IdempotencyKey must be")

    def test_inline_key_does_not_replace_shared_reference(self):
        self.post["parameters"].remove(KEY)
        self.post["parameters"].append(copy.deepcopy(self.spec["components"]["parameters"]["IdempotencyKey"]))
        self.assert_invalid("POST must reference")

    def test_path_parameter_inheritance_and_operation_override(self):
        item = self.spec["paths"][self.path]
        item["parameters"] = [KEY]
        self.post["parameters"].remove(KEY)
        self.assertEqual(check_spec(self.spec), [])
        self.post["parameters"].append({"name": "idempotency-key", "in": "header", "required": False,
                                        "schema": {"type": "string"}})
        self.assert_invalid("POST must reference")

    def test_duplicate_header_parameter_is_rejected(self):
        self.post["parameters"].append(KEY)
        self.assert_invalid("duplicate parameter")

    def test_each_conflict_code_must_have_a_structured_example(self):
        examples = self.spec["components"]["responses"]["Conflict"]["content"]["application/json"]["examples"]
        for name in list(examples):
            with self.subTest(name=name):
                removed = examples.pop(name)
                self.assert_invalid("examples must include IDEMPOTENCY_CONFLICT and REQUEST_IN_PROGRESS")
                examples[name] = removed

    def test_retry_after_header_and_seconds_schema_are_required(self):
        conflict = self.spec["components"]["responses"]["Conflict"]
        header = conflict["headers"].pop("Retry-After")
        self.assert_invalid("409 must declare Retry-After")
        conflict["headers"]["retry-after"] = header
        self.assertEqual(check_spec(self.spec), [])
        for schema in [{"type": "string"}, {"type": "integer"}, {"type": "integer", "minimum": -1},
                       {"type": "integer", "minimum": 0, "nullable": True}]:
            with self.subTest(schema=schema):
                self.spec["components"]["headers"]["RetryAfter"]["schema"] = schema
                self.assert_invalid("409 Retry-After must be")

    def test_inline_and_local_referenced_response_objects(self):
        self.post["responses"]["409"] = copy.deepcopy(self.spec["components"]["responses"]["Conflict"])
        self.spec["components"]["schemas"]["KeyUUID"] = {"type": "string", "format": "uuid"}
        self.spec["components"]["parameters"]["IdempotencyKey"]["schema"] = {
            "$ref": "#/components/schemas/KeyUUID"}
        self.assertEqual(check_spec(self.spec), [])

    def test_bad_references_fail_closed_without_external_reads(self):
        for ref in ["https://example.invalid/spec.yaml#/Conflict", "other.yaml#/Conflict",
                    "#/components/responses/Missing", "#/components/responses/Conflict"]:
            with self.subTest(ref=ref):
                self.spec["components"]["responses"]["Conflict"] = {"$ref": ref}
                self.assert_invalid(f"POST {self.path}:")

    def test_other_methods_and_path_item_refs(self):
        self.spec["paths"]["/read-only"] = {"get": {"responses": {"200": {"description": "OK"}}}}
        self.spec["components"]["pathItems"] = {"phone": self.spec["paths"][self.path]}
        self.spec["paths"][self.path] = {"$ref": "#/components/pathItems/phone"}
        self.assertEqual(check_spec(self.spec), [])

    def test_ci_command_status_and_diagnostics(self):
        invalid = copy.deepcopy(self.spec)
        invalid["paths"][self.path]["post"]["parameters"].remove(KEY)
        for content, expected, diagnostic in [
            (yaml.safe_dump(self.spec), 0, "POST idempotency declarations are valid"),
            (yaml.safe_dump(invalid), 1, f"POST {self.path}: POST must reference"),
            ("paths: [", 1, "cannot read OpenAPI"),
            ("null", 1, "spec:"),
        ]:
            with self.subTest(diagnostic=diagnostic), tempfile.TemporaryDirectory() as directory:
                spec = Path(directory) / "spec.yaml"
                spec.write_text(content, encoding="utf-8")
                result = subprocess.run([sys.executable, str(ROOT / "scripts/check_idempotency.py"), str(spec)],
                                        text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertIn(diagnostic, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
