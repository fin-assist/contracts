#!/usr/bin/env python3
"""Posts the result of the Codex review workflow (.github/workflows/codex-review.yml) as a PR review.

Codex returns JSON matching the schema in the workflow: a Russian summary and findings with `path`, `line` and
`body`. Findings on lines inside the PR diff become inline comments (review conversations that can be resolved);
the rest go into the review body, so no finding is lost when Codex names a line outside the diff. Output that is
not valid JSON is posted as a plain review body.

Runs from the default branch checkout, never from the PR's code: it holds a token that can write to the PR.

Usage (environment: GITHUB_TOKEN, GITHUB_REPOSITORY):
  python3 scripts/codex-review-post.py --pr 42 --commit <head sha> --model gpt-6-astra --result result.json
  python3 -m unittest discover -s scripts -p 'test_*.py'   # tests

Standard library only, like the other scripts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

API = "https://api.github.com"
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
# Marker the agents look for when waiting for the Codex review (AGENTS.md, Workflow).
MARKER = "<!-- codex-review-workflow -->"
# GitHub attaches a review posted without commit_id to the PR's head at that moment, which may be a newer commit.
FALLBACK_NOTE = ("<sub>Опубликовано без привязки к коммиту: GitHub отклонил исходный review. Проверен коммит "
                 "`{commit}`, а не тот, к которому GitHub прикрепил это review.</sub>")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    body: str


def diff_lines(patch: str | None) -> set[int]:
    """Line numbers of the new file that a review comment can target: added and context lines of the hunks."""
    lines: set[int] = set()
    if not patch:
        return lines
    current = None
    for raw in patch.split("\n"):
        hunk = _HUNK.match(raw)
        if hunk:
            current = int(hunk.group(1))
            continue
        if current is None or raw.startswith("-") or raw.startswith("\\"):
            continue
        if raw.startswith("+") or raw.startswith(" ") or raw == "":
            lines.add(current)
            current += 1
    return lines


def parse_result(text: str) -> tuple[str, list[Finding]] | None:
    """Summary and findings from Codex output; None when the output is not the expected JSON."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("summary"), str):
        return None
    findings = []
    for item in data.get("findings") or []:
        if not isinstance(item, dict):
            continue
        path, line, body = item.get("path"), item.get("line"), item.get("body")
        if isinstance(path, str) and isinstance(line, int) and not isinstance(line, bool) and isinstance(body, str):
            findings.append(Finding(path.removeprefix("./"), line, body.strip()))
    return data["summary"].strip(), findings


def split_findings(findings: list[Finding], files: dict[str, set[int]]) -> tuple[list[Finding], list[Finding]]:
    """(inline, outside): a finding is inline when its line is in the diff of its file."""
    inline, outside = [], []
    for f in findings:
        (inline if f.line in files.get(f.path, set()) else outside).append(f)
    return inline, outside


def review_body(summary: str, outside: list[Finding], model: str, commit: str) -> str:
    parts = [MARKER, f"### Ревью Codex (`{model}`)", "", summary or "Сводка не получена."]
    if outside:
        parts += ["", "#### Замечания вне диффа", ""]
        parts += [f"- `{f.path}:{f.line}` — {f.body}" for f in outside]
    parts += ["", f"<sub>Коммит `{commit[:7]}` · `.github/workflows/codex-review.yml`</sub>"]
    return "\n".join(parts)


def raw_body(text: str, model: str, commit: str) -> str:
    return "\n".join([
        MARKER, f"### Ревью Codex (`{model}`)", "",
        "Ответ не в ожидаемом формате JSON, публикуется как есть:", "", text.strip() or "(пустой ответ)", "",
        f"<sub>Коммит `{commit[:7]}` · `.github/workflows/codex-review.yml`</sub>",
    ])


def build_review(text: str, files: dict[str, set[int]], model: str, commit: str) -> dict:
    """Payload for POST /pulls/{n}/reviews."""
    parsed = parse_result(text)
    if parsed is None:
        return {"commit_id": commit, "event": "COMMENT", "body": raw_body(text, model, commit)}
    summary, findings = parsed
    inline, outside = split_findings(findings, files)
    review = {"commit_id": commit, "event": "COMMENT", "body": review_body(summary, outside, model, commit)}
    if inline:
        review["comments"] = [{"path": f.path, "line": f.line, "side": "RIGHT", "body": f.body} for f in inline]
    return review


def fallback_review(text: str, review: dict, model: str, commit: str) -> dict:
    """The review to retry after a 422: no inline comments, no commit_id, every finding in the body."""
    moved = [Finding(c["path"], c["line"], c["body"]) for c in review.get("comments", [])]
    parsed = parse_result(text)
    if parsed is None:
        body = review["body"]
    else:
        summary, findings = parsed
        outside = [f for f in findings if f not in moved] + moved
        body = review_body(summary, outside, model, commit)
    return {"event": "COMMENT", "body": body + "\n\n" + FALLBACK_NOTE.format(commit=commit[:7])}


# --- GitHub REST ----------------------------------------------------------------------------------------------

def _request(method: str, url: str, token: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read() or b"null")


def pr_files(repo: str, pr: int, token: str) -> dict[str, set[int]]:
    files: dict[str, set[int]] = {}
    page = 1
    while True:
        batch = _request("GET", f"{API}/repos/{repo}/pulls/{pr}/files?per_page=100&page={page}", token)
        for f in batch:
            files[f["filename"]] = diff_lines(f.get("patch"))
        if len(batch) < 100:
            return files
        page += 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--result", required=True, help="file with the final message of Codex")
    args = parser.parse_args(argv)
    token, repo = os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"]
    with open(args.result, encoding="utf-8") as fh:
        text = fh.read()

    review = build_review(text, pr_files(repo, args.pr, token), args.model, args.commit)
    url = f"{API}/repos/{repo}/pulls/{args.pr}/reviews"
    try:
        _request("POST", url, token, review)
    except urllib.error.HTTPError as err:
        # 422: GitHub rejects a comment position or the commit itself (a force-push dropped it from the PR). Retry
        # once with every finding in the body and without commit_id, so GitHub takes the PR's latest commit; the body
        # still names the reviewed commit.
        if err.code != 422:
            raise
        review = fallback_review(text, review, args.model, args.commit)
        _request("POST", url, token, review)
    print(f"Posted review: {len(review.get('comments', []))} inline comment(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
