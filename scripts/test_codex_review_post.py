"""Tests of scripts/codex-review-post.py. Run: python3 -m unittest discover -s scripts -p 'test_*.py'"""

import importlib.util
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("codex_review_post", ROOT / "scripts" / "codex-review-post.py")
post = importlib.util.module_from_spec(_spec)
sys.modules["codex_review_post"] = post  # dataclasses resolve the module by name
_spec.loader.exec_module(post)

PATCH = "\n".join([
    "@@ -10,4 +10,5 @@ class A {",
    " keep",          # 10
    "-removed",
    "+added one",     # 11
    "+added two",     # 12
    " keep",          # 13
    "\\ No newline at end of file",
    "@@ -40,2 +41,2 @@",
    " ctx",           # 41
    "+new",           # 42
])

RESULT = json.dumps({
    "summary": "Безопасность: не затрагивает.",
    "findings": [
        {"path": "app/A.kt", "line": 12, "body": "Строка в диффе"},
        {"path": "./app/A.kt", "line": 42, "body": "Путь с ./"},
        {"path": "app/A.kt", "line": 30, "body": "Строка вне диффа"},
        {"path": "app/Other.kt", "line": 1, "body": "Файл вне PR"},
        {"path": ".github/workflows/x.yml", "line": 3, "body": "Скрытый каталог"},
    ],
})


class DiffLinesTest(unittest.TestCase):
    def test_added_and_context_lines_of_every_hunk(self):
        self.assertEqual(post.diff_lines(PATCH), {10, 11, 12, 13, 41, 42})

    def test_removed_lines_do_not_advance_new_file_numbers(self):
        self.assertNotIn(14, post.diff_lines(PATCH))

    def test_no_patch_for_binary_or_huge_file(self):
        self.assertEqual(post.diff_lines(None), set())
        self.assertEqual(post.diff_lines(""), set())


class ParseResultTest(unittest.TestCase):
    def test_keeps_valid_findings_and_normalises_dot_slash(self):
        summary, findings = post.parse_result(RESULT)
        self.assertEqual(summary, "Безопасность: не затрагивает.")
        self.assertEqual(findings[1].path, "app/A.kt")
        self.assertEqual(findings[4].path, ".github/workflows/x.yml")

    def test_skips_malformed_findings(self):
        text = json.dumps({"summary": "s", "findings": [
            {"path": "a", "line": "3", "body": "b"}, {"path": "a", "line": True, "body": "b"}, "x",
            {"path": "a", "line": 3, "body": "ok"},
        ]})
        self.assertEqual(post.parse_result(text)[1], [post.Finding("a", 3, "ok")])

    def test_not_json_or_wrong_shape_is_none(self):
        self.assertIsNone(post.parse_result("Didn't find anything"))
        self.assertIsNone(post.parse_result(json.dumps(["summary"])))
        self.assertIsNone(post.parse_result(json.dumps({"findings": []})))

    def test_missing_findings_is_empty_list(self):
        self.assertEqual(post.parse_result(json.dumps({"summary": "ok"})), ("ok", []))


class BuildReviewTest(unittest.TestCase):
    FILES = {"app/A.kt": post.diff_lines(PATCH), ".github/workflows/x.yml": {3}}

    def test_lines_in_diff_become_inline_comments_rest_go_to_body(self):
        review = post.build_review(RESULT, self.FILES, "gpt-6-astra", "abcdef1234567")
        self.assertEqual(review["commit_id"], "abcdef1234567")
        self.assertEqual(review["event"], "COMMENT")
        self.assertEqual(
            [(c["path"], c["line"], c["side"]) for c in review["comments"]],
            [("app/A.kt", 12, "RIGHT"), ("app/A.kt", 42, "RIGHT"), (".github/workflows/x.yml", 3, "RIGHT")],
        )
        body = review["body"]
        self.assertTrue(body.startswith(post.MARKER))
        self.assertIn("`app/A.kt:30` — Строка вне диффа", body)
        self.assertIn("`app/Other.kt:1` — Файл вне PR", body)
        self.assertNotIn("Строка в диффе", body)
        self.assertIn("`gpt-6-astra`", body)
        self.assertIn("`abcdef1`", body)

    def test_no_findings_posts_summary_without_comments(self):
        review = post.build_review(json.dumps({"summary": "Замечаний нет.", "findings": []}), {}, "m", "c" * 40)
        self.assertNotIn("comments", review)
        self.assertIn("Замечаний нет.", review["body"])
        self.assertNotIn("Замечания вне диффа", review["body"])

    def test_empty_summary_and_empty_output_get_placeholders(self):
        review = post.build_review(json.dumps({"summary": "  ", "findings": []}), {}, "m", "c" * 40)
        self.assertIn("Сводка не получена.", review["body"])
        self.assertIn("(пустой ответ)", post.build_review("  \n", {}, "m", "c" * 40)["body"])

    def test_unexpected_output_is_posted_as_is(self):
        review = post.build_review("plain text answer", {}, "m", "c" * 40)
        self.assertNotIn("comments", review)
        self.assertIn("plain text answer", review["body"])
        self.assertTrue(review["body"].startswith(post.MARKER))


class MainTest(unittest.TestCase):
    def _run(self, responses):
        calls = []

        def fake(method, url, token, payload=None):
            calls.append((method, url, payload))
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            fh.write(RESULT)
        env = {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}
        with mock.patch.dict("os.environ", env), mock.patch.object(post, "_request", side_effect=fake), \
                mock.patch("sys.stdout", io.StringIO()):
            post.main(["--pr", "7", "--commit", "abc1234", "--model", "gpt-6-astra", "--result", fh.name])
        return calls

    def test_posts_review_with_inline_comments(self):
        files = [{"filename": "app/A.kt", "patch": PATCH}]
        calls = self._run([files, {}])
        self.assertEqual(calls[0][:2], ("GET", f"{post.API}/repos/o/r/pulls/7/files?per_page=100&page=1"))
        method, url, payload = calls[1]
        self.assertEqual((method, url), ("POST", f"{post.API}/repos/o/r/pulls/7/reviews"))
        self.assertEqual(len(payload["comments"]), 2)

    def test_rejected_positions_fall_back_to_body_without_losing_findings(self):
        files = [{"filename": "app/A.kt", "patch": PATCH}]
        rejected = urllib.error.HTTPError("u", 422, "Unprocessable", {}, None)
        calls = self._run([files, rejected, {}])
        payload = calls[2][2]
        self.assertNotIn("comments", payload)
        self.assertNotIn("commit_id", payload)  # a force-pushed commit would be rejected again
        self.assertIn("`abc1234`", payload["body"])  # the body still names the reviewed commit
        self.assertIn("без привязки к коммиту", payload["body"])
        for text in ("Строка в диффе", "Путь с ./", "Строка вне диффа", "Файл вне PR", "Скрытый каталог"):
            self.assertIn(text, payload["body"])

    def test_other_http_errors_are_raised(self):
        files = [{"filename": "app/A.kt", "patch": PATCH}]
        with self.assertRaises(urllib.error.HTTPError):
            self._run([files, urllib.error.HTTPError("u", 403, "Forbidden", {}, None)])

    def test_422_without_inline_comments_retries_without_commit(self):
        files = [{"filename": "app/Unrelated.kt", "patch": PATCH}]  # every finding lands outside the diff
        calls = self._run([files, urllib.error.HTTPError("u", 422, "Unprocessable", {}, None), {}])
        self.assertIn("commit_id", calls[1][2])
        self.assertNotIn("commit_id", calls[2][2])
        self.assertIn("Файл вне PR", calls[2][2]["body"])

    def test_second_422_is_raised(self):
        files = [{"filename": "app/A.kt", "patch": PATCH}]
        rejected = urllib.error.HTTPError("u", 422, "Unprocessable", {}, None)
        with self.assertRaises(urllib.error.HTTPError):
            self._run([files, rejected, urllib.error.HTTPError("u", 422, "Unprocessable", {}, None)])

    def test_unexpected_output_fallback_keeps_raw_body(self):
        review = post.build_review("plain text", {}, "m", "abc1234")
        retry = post.fallback_review("plain text", review, "m", "abc1234")
        self.assertEqual(set(retry), {"event", "body"})
        self.assertTrue(retry["body"].startswith(review["body"]))
        self.assertIn("без привязки к коммиту", retry["body"])
        self.assertIn("`abc1234`", retry["body"])

    def test_pages_through_files(self):
        page1 = [{"filename": f"f{i}", "patch": None} for i in range(100)]
        calls = self._run([page1, [{"filename": "app/A.kt", "patch": PATCH}], {}])
        self.assertTrue(calls[1][1].endswith("page=2"))


if __name__ == "__main__":
    unittest.main()
