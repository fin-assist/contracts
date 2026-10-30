# Instructions for code agents

API contracts of «Понятные финансы» (B2C personal finance: OFX statement import from T-Bank, operations, analytics,
LLM assistant): the client REST API (mobile client ↔ `bank-bff`) and the internal gRPC between Go services. This
repository is the source of truth for them. Read by Claude Code (through `CLAUDE.md`) and by Codex. This file is the
orientation map: read it and `README.md` first, then open only the sections the task touches — `docs/api.md` is long.

## Keep this file current

A PR that changes what this file or `README.md` says (layout, CI checks, conventions, process) updates them in the
same PR.

## Where things are decided

- Here: `docs/api.md` (meaning of fields — wins over `openapi/openapi.yaml` when they differ), `openapi/openapi.yaml`,
  `docs/calc.md` (how the fields are calculated — accounting rules and metrics, FIN-158), `docs/grpc-api.md`, `proto/`.
  For calculation the order is: the owner's latest decision → `docs/calc.md` → `docs/api.md` / `openapi.yaml` →
  `fixtures/calc` (an executable copy of the first two; its `categories.json` is also the list of known bank
  categories the services use, FIN-200). Confluence PF (https://finassist.atlassian.net/wiki/spaces/PF) holds only a summary
  and a link here; MVP scope, architecture and product decisions live there («Состав MVP», «Архитектура»,
  «Продуктовые решения»).
- Version 0.2 is crystallized: every change goes through a YouTrack issue (`FIN-<n>`, finassist.youtrack.cloud) and
  is referenced in the PR.
- A contract change is two issues: one for the contract (this repo) and one for the client (fin-assist/android,
  including its fake backend `:mock:backend`); the client issue depends on the contract issue.
- Mismatches between mockups, docs and the contract are expected. Resolve them in favour of `docs/api.md` + the
  latest decision, say so in the PR, propose (not impose) changes.
- There is no backend yet: the Android `mock` flavor implements this contract in-process. The backend (Go,
  PostgreSQL; one repository per service and per library) will be written against it and imports the Go code
  generated here: module `github.com/fin-assist/contracts/gen/go`, released by tags `gen/go/vX.Y.Z` (`README.md`,
  «Go-модуль `gen/go`»).

## Repository map

| What | Where |
|---|---|
| General rules of the REST API: format, money, IDs, notation (`enum` / `code` / text), auth, headers, idempotency, errors, SSE, extensibility | `docs/api.md`, «Общие правила» |
| Block catalog shared by Analytics and the assistant | `docs/api.md`, «Блоки» |
| Endpoints | `docs/api.md`, sections 1–7 (`/v1/auth`, `/v1/profile`, `/v1/statements`, `/v1/operations`, `/v1/categories`, `/v1/analytics`, `/v1/assistant`) |
| Error registry | `docs/api.md`, section 8; `proto/finassist/common/v1/errors.proto` (`ErrorReason`), `error_options.proto` |
| Extensibility decisions | `docs/api.md`, «Расширяемость» and «Решения по расширяемости» |
| OpenAPI 3.0.3 of the same API | `openapi/openapi.yaml`; lint config `redocly.yaml` |
| Calculation rules: transfer pairs, refunds, coverage and gaps, Analytics metrics, thresholds, order and composition of blocks | `docs/calc.md` |
| Services, call models (user / system), service identity, metadata, errors, idempotency, outbox | `docs/grpc-api.md` |
| Service contracts | `proto/finassist/<service>/v1/*.proto`, shared types `proto/finassist/common/v1/` |
| Source revisions and assistant answer freshness (FIN-367) | `common/v1/data_revision.proto`, `statementdata/v1/internal.proto`, analytics snapshot/state/read provenance; policy and rollout — `docs/grpc-api.md`, «Ревизии исходных данных ответа помощника»; clients FIN-358/368/369/370 |
| Registry check | `proto/scripts/check_error_registry.py` |
| POST idempotency declarations and regression tests (FIN-77) | `scripts/check_idempotency.py`, `scripts/test_idempotency.py` |
| Generated Go module `github.com/fin-assist/contracts/gen/go` (messages, gRPC, `finerr`) | `gen/go/` — generated, never edited by hand (except `go.mod` and tests); config `proto/buf.gen.yaml` |
| Pinned codegen tools (`buf`, `protoc-gen-go`, `protoc-gen-go-grpc`) and the `finerr` generator | `tools/go.mod`, `tools/protoc-gen-finerr/`; run through `scripts/generate.sh` |
| Actual Go OpenAPI engine compatibility, shared error fixtures, Unicode boundaries and optional/non-null fields | `tools/openapicheck/` — test-only `kin-openapi` (version in `tools/go.mod`) dependency, run by the existing tools race-test job |
| Calculation fixtures: statements, scenario, expected responses, comparison rules (FIN-107); the list of known bank categories (`categories.json`, FIN-200) | `fixtures/calc/` (format and rules — `fixtures/calc/README.md`) |
| Error wire examples and OpenAPI boundaries (FIN-224): REST/SSE/history, old responses, open codes/severity, Unicode text, REST retryable boolean | `fixtures/errors/presentation.json`, `scripts/test_error_presentation.py` |
| Reference calculation that produces the expected responses (`docs/calc.md`) | `tools/refcalc/` (Python, stdlib only; tests — spec examples) |
| Go coverage reports and regression tests | `scripts/coverage-diff.py`, `scripts/test_coverage_diff.py` |
| PR review by Claude | `.github/workflows/claude.yml` |
| Pre-provider personal-data consent and 14+ confirmation (FIN-363) | `docs/api.md` 1.1–1.3, `user/v1/user.proto`; durable evidence, pinned version, old attempts, PHONE_VERIFICATION_UNKNOWN_TIMEOUT (default 5m, immutable per-attempt deadline) with owner-accepted late-call/duplicate risk, separate 24-hour abandoned proof retention and rollout — `docs/grpc-api.md` 7.1; consumers FIN-364/365 |

## Checks

CI (`.github/workflows/ci.yml`, table in `README.md`): `redocly lint`, `oasdiff breaking` against the PR base,
`buf lint`, `buf breaking`, `check_error_registry.py`, `check_error_spec_stable.py` (error_spec of existing codes
unchanged against the PR base; the owner accepts a change with `breaking-accepted`), and the Go module job (`gen/go` equals a fresh
`scripts/generate.sh`, `go build`, `go vet`, race tests with coverage in both `gen/go` and `tools`,
and `scripts/test_coverage_diff.py`). Run them locally before pushing (commands in `README.md`,
«CI»). A PR that changes `proto/` commits the regenerated `gen/go`. `buf` is pinned twice — `tools/go.mod` and
`BUF_VERSION` in `ci.yml` — and CI fails when they differ. The calculation fixtures job runs the reference
calculation tests and requires `fixtures/calc/expected` to equal a fresh scenario run that passes `openapi.yaml`
(block `data` against its primitive's schema) and covers every response field — an uncovered field needs a scenario
step, or an entry with a reason in `fixtures/calc/coverage-exceptions.json` (FIN-157).

The `openapi-lint` job also runs `scripts/check_idempotency.py openapi/openapi.yaml` and
`python3 -m unittest discover -s scripts -p 'test_idempotency.py'` using PyYAML. Every POST must effectively
reference the shared required UUID header `#/components/parameters/IdempotencyKey` (path-level inheritance is
allowed), or declare `x-idempotency: not-required` with a nonblank `x-idempotency-reason`. Review the reason;
CI cannot infer side effects. A keyed POST must declare a 409 JSON response with examples for both
IDEMPOTENCY_CONFLICT and REQUEST_IN_PROGRESS, and Retry-After as non-null integer seconds with minimum 0.
Local references are resolved without accessing files or the network; this lint does not prove runtime behavior.
Path Item fields beside `$ref` are retained across reference chains; overlapping fields fail because their
semantics are undefined in OpenAPI 3.0.3.
A PR that changes calculation rules changes `docs/calc.md` (and `docs/api.md` when the meaning of analytics, feed or
import fields changes), updates `tools/refcalc` and regenerates `fixtures/calc/expected` in the same PR, and says which
responses changed. A rule that lives only in `tools/refcalc` or `fixtures/calc/README.md` is a defect: rules go to
`docs/calc.md`, the fixtures README keeps only how responses are compared.

A breaking change fails `oasdiff` / `buf breaking`. In the alpha the owner may accept it with the PR label
`breaking-accepted` — for REST (`oasdiff`) and for a changed `error_spec` of an existing code
(`check_error_spec_stable.py`); `buf breaking` does not read labels. Never add that label yourself — ask in the PR.

The calculation fixtures job also runs `python3 -m unittest discover -s scripts -p 'test_error_presentation.py'`
with its existing PyYAML and OpenAPI validator dependencies. This validates the FIN-224 producer schema and
compatibility examples; client display/fallback behavior belongs to FIN-223. BFF owns safe user text/severity,
not internal services or generated `finerr`; existing assistant proto text fields are compatibility-only
(`docs/grpc-api.md`, 5.4). The four new presentation fields are optional but non-null (`optional<T>` in
`docs/api.md`); producers omit absent values, including both new error fields on non-failed history messages.
Existing nullable fields keep their semantics. Presentation must not change special actions or retry metadata.

REST `Error.retryable` is a separate optional/non-null boolean combining request-specific retry safety AND
permission; it does not declare global endpoint idempotency or extend key validity. BFF computes it from the
service-owned original-RPC permission (`common.v1.RequestRetryInfo`) AND verified safety of the WHOLE outer
REST call; no proof/absent advice means false. Do not infer business permission by reason/code in the BFF. Existing assistant SSE/history retryable
still authorizes RetryAnswer (new generation/key), not resending the original AskQuestion. Generic request advice
never authorizes a new operation attempt. Preserve documented auth/maintenance recovery flows separately from
unchanged-context direct retry, with the same original key and actual safety/TTL checks. Schema tests cover
true/false/absence and malformed values locally; actual retry-policy/client behavior belongs to FIN-225/FIN-223.

FIN-224 reuses google.rpc.Status and standard details for unary and nested result failures. New additive fields
are AnswerError.status=6, Message.error=14 and ImportStreamError.status=3; old fields/events/types/numbers dual-write
until decoder rollout and recorded rollback floors. Initial nested profile forbids RequestRetryInfo and RetryInfo:
reading the failed resource succeeded; domain new-attempt permission/quotas remain outside the common error.
Absent canonical payload uses the legacy adapter; valid open reason wins; present-invalid fails safely without
consulting the legacy code: assistant terminal/history project INTERNAL_ERROR (history non-null), import projects
PROCESSING_FAILED; explicit outer domain retryable stays service-owned. Actions are scoped by shape/endpoint:
REST registry actions never activate just because a nested result reason has the same spelling. Keep the generic
result decoder open; no unary registry lookup or namespace blacklist. ErrorEvent is the neutral external schema; keep SseError as a ref alias.
Generator owns WithRequestRetryInfo, REST.RequestRetryable and NewResultStatus/DecodeResultStatus. Preserve recognized
detail validation, unknown Any compatibility and wrapped-status diagnostics protection. This is a generated Go API
addition: owner-created immutable gen/go tag after merge precedes dependent backend/library PRs. Existing error_spec
and constructor signatures stay unchanged. The cached phone429 advice-loss path in go-idempotency needs a separate
rollout prerequisite; do not claim that library/service change implemented here.

Clock rules live in docs/api.md, “Время ожидания и часы”: fresh server duration plus sleep-aware monotonic elapsed;
BFF Date on fresh responses including errors; same-response Date fallback is approximate UI, never authorization,
permission or key-age proof. No new server_time fields, no wall-clock countdown or restart of stale duration after
a lost anchor/reboot. Runtime behavior tests belong to consumers.

`tools/openapicheck` validates the full specification and the shared error fixtures with the actual Go engine
selected for REST (`kin-openapi`, pinned in tools/go.mod). Stdlib has no OpenAPI
validator; this dependency is test-only in tools, not in generated/runtime APIs. Full-document validation includes
embedded examples, and every shared error fixture is also explicitly validated with `VisitJSON`. FIN-339 tests
check the six JSON-body routes, their minimum supported 65536-byte transport budget and REQUEST_TOO_LARGE response.
Shared raw HTTP examples in `fixtures/requests/body-policy.json` cover actual zero-byte/chunked requests, unexpected
nonempty bodies and supported/unsupported Content-Encoding. BFF FIN-268 tests enforce the bodyless policy;
kin-openapi alone does not reject a body on an operation without requestBody. Existing protections of other
body-bearing routes remain: bounded reads and transport 413. For other JSON operations the refusal code and
byte budget are not yet defined by the contract; agreement belongs to FIN-225, outside FIN-268 implementation.
The new code/minimum apply only to the six routes; separately documented file limits/codes stay unchanged.
This BFF-only code is absent from ErrorReason/error_spec/gen/go; FILE_TOO_LARGE remains file-only.

## Writing the contract

The rules below are the ones the review checks (Code Review Rules); the full text is in `docs/api.md`.

- Language: `docs/*.md`, descriptions in `openapi.yaml` and comments in `.proto` are in Russian (`.proto` comments in
  domain terms, without UI elements — `docs/grpc-api.md`, «Время, деньги, перечисления»); field names, codes, proto
  identifiers, comments in workflows and scripts, and commit messages are in English.
- Change `docs/api.md`, `openapi.yaml` and, when a service is involved, `docs/grpc-api.md` and `proto/` in the same
  PR. An endpoint, field or code that exists in one of them and not in the other is a defect.
- Every field in `docs/api.md` has a type in the notation («Нотация»), says when it is absent or `null` (`T?`), and
  what the client does with an unknown value when it is a `code`. A non-obvious rule gets an example (JSON or a
  table row), not only prose.
- Extensibility (decision of 13.10.2026): grow by addition; open sets are `code` (`x-extensible-enum`), `enum` only
  for a set that cannot grow; texts for codes the client renders; `value_kind` for values, `currency` for amounts;
  lists of entities instead of a field per entity; no field name reused with another shape across block types;
  `deprecated` only by «Удаление устаревшего». A breaking change needs a new API version (`/v2`) or, in the alpha
  and for REST or a changed `error_spec` of an existing code only, the owner's `breaking-accepted`; `buf breaking`
  does not read labels, so any other breaking `.proto` change goes through `reserved` and a new field or method.
- Errors: BFF-only transport codes (MAINTENANCE, CLIENT_VERSION_UNSUPPORTED, REQUEST_TOO_LARGE) live in section 8
  and OpenAPI, with an explicit checker exception; service codes go into section 8 of `docs/api.md` and `ErrorReason` together, with HTTP status, `error_spec`
  and client behaviour; removed numbers and names go to `reserved`.
- Idempotency: every new `POST` that creates a resource or has a side effect requires `Idempotency-Key`; say which
  responses are stored («Идемпотентность»).
- Every new endpoint states who may call it (token or not, which consent), its limits (size, count, rate) and its
  errors.

### Coverage reports (FIN-210)

CI reports project totals and changed executable lines together from the same profile per Go module in one
updated PR comment and the run summary; push runs retain project totals. Both percentages are informational here; no new coverage target is introduced for generated or handwritten code. Count unique lines in blocks with nonzero statement counts, including generated production code;
exclude `_test.go` and `testdata/`. An empty profile means no data, never 100%. Fork PRs cannot post comments.
Test changes to the report with `python3 -m unittest discover -s scripts -p 'test_coverage_diff.py'`.

## SQL construction in Go

Applies when handwritten Go code in this repository needs to build SQL queries.

- **SQL construction:** write fixed-structure queries as plain SQL with parameters (`$1`, `$2`, … for
  PostgreSQL). Use [Squirrel](https://github.com/Masterminds/squirrel) (`github.com/Masterminds/squirrel`) only
  when assembling a query dynamically, such as optional predicates or varying column lists. Changing argument
  values alone does not make a query dynamic. This limited use is an approved exception to "standard library
  first". Parameterize values; keep identifiers and structural fragments constant or from an explicit allowlist.
  Use `PlaceholderFormat(sq.Dollar)` for PostgreSQL builders. If a dynamic query cannot be expressed by the
  builder, explain the limitation beside its manual construction and preserve parameterization.

## Workflow

- Branches: `<kind>/<slug>` — `feature/…`, `fix/…`, `tooling/…`. No release branches here: a PR targets `main`.
  Commits: imperative English subject (`Add …`, `Fix …`, `Address review: …`).
- PR title and description in Russian: what changed and why, which client issue follows, deviations from
  mockups/docs, how it was checked. A PR for a single YouTrack issue starts its title with the issue key
  (`FIN-42 Курсор в поиске операций`); a PR for several issues lists all of them in the description, each linked
  (`https://finassist.youtrack.cloud/issue/FIN-<n>`). No issue — no key in the title.
- After the PR is open and all planned changes are pushed, request a review with one PR comment in Russian
  addressed to both reviewers: it starts with `@codex @claude`, and the text after them is addressed to both (never
  «@claude, …» alone — Codex reads the same comment):

  ```
  @codex @claude проведите ревью этого PR по Code Review Rules в AGENTS.md, включая разделы Security и
  Extensibility. Замечания — на русском, inline на изменённых строках. Не придирайтесь к формулировкам
  документации: стиль, словоупотребление, опечатки и неточности, не меняющие смысл, не комментируйте — замечание по
  тексту только если формулировка искажает смысл поля или поведения, допускает двойное прочтение при реализации
  или противоречит openapi/proto либо другой части документации; на проходы Security и Extensibility это
  ограничение не распространяется. Особое внимание: <что именно и почему>.
  ```

  - `@claude` — `.github/workflows/claude.yml` (runs from the default branch), Claude Opus 5.5 (`claude-opus-5-5`,
    with `--effort high`, set in `claude_args`; FIN-204). Needs the Claude GitHub App and the
    `CLAUDE_CODE_OAUTH_TOKEN` secret.
  - `@codex` — the Codex GitHub integration, under the owner's ChatGPT subscription. Its model is chosen by OpenAI and
    cannot be pinned from here; a pinned model would need the OpenAI API with per-token billing, which was declined
    (FIN-103).
- Waiting for the review: when the session receives the PR's GitHub events (reviews, comments, CI), react to them
  instead of polling. Otherwise check 3 minutes after the request, then every 2 minutes. A reviewer has answered when
  it posted its review or summary for the latest commit; for Codex (`chatgpt-codex-connector[bot]`) «Didn't find any
  major issues» or a 👍 reaction without a comment also counts. If a reviewer has not answered within 15 minutes of
  the last request, say so in the PR and to the owner and stop waiting for it. The cycle ends when both reviewers have answered the latest
  request and no review thread is open.
- Address review findings in follow-up commits on the same branch, reply in the conversation and resolve it; then
  request a review again and wait the same way. Agents never merge a PR; the owner does.

## Code Review Rules

Read by `@claude` (through `CLAUDE.md`) and by the Codex integration, which looks for this heading. Two areas get a
dedicated pass on every PR: Security and Extensibility (below). A contract outlives
its first implementation: a flaw merged here is copied into the backend and every client.

### General

- Write all review comments in Russian (summary and inline comments). Field names, codes, identifiers and quotes
  stay as they are.
- Report only verified problems with a concrete failure scenario: which client or service, which request or
  response, what goes wrong. Skip style nits and wording preferences.
- Wording of the documentation (`docs/*.md`, descriptions in `openapi.yaml`, comments in `.proto`) is reviewed for
  meaning only. Do not comment on style, word choice, word order, typos and inaccuracies that do not change what a
  client or service implementer would build. A comment on the text is justified only when the wording distorts the
  meaning of a field or behaviour, allows two readings that lead to different implementations, or contradicts
  `openapi.yaml` / `proto/` / another part of the docs, and the comment names that failure. This limit does not apply
  to the Security and Extensibility passes.
- Post each finding as an inline comment on the changed line (a review conversation that can be resolved), not only
  in a summary comment. The summary lists what was checked, with a «Безопасность» part and a «Расширяемость» part;
  «not affected» is a valid answer for either, with one line why.
- Check that the PR updates `README.md` and this file when it changes what they describe, and that it references
  its YouTrack issue (and names the client issue for a contract change).

### Security

The API carries a user's bank operations, phone number, statement files and assistant dialogue (personal data under
152-ФЗ) and spends paid resources (calls, LLM). A finding names what leaks or breaks and how a client, another user
or a compromised service gets to it. Check the changed contract for:

- Authorization and object access: every endpoint states its auth (`Authorization`, or one of the exceptions of «Аутентификация»: `/v1/auth/*`
  except logout, `GET /v1/consents/personal_data` before sign-in; `assistant` there needs the token);
  the user comes from the token (`sub`), never from a path, query or body field of a user call; an object ID of
  another user gives `404`, not its data or a `403` that confirms it exists (IDOR / BOLA). System calls in gRPC take
  `user_id` explicitly and are allowed only to the callers in the table of `docs/grpc-api.md` («Идентичность сервиса»).
- Secrets and tokens only in headers or the body, never in the path or query (they land in logs and proxies);
  responses that hold secrets are not stored for idempotency longer than needed (token refresh — 60 s); logging
  interceptors mask the keys named in `docs/grpc-api.md` («Metadata»).
- Data minimisation: a response returns only what the screen needs; no internal IDs, other users' data, raw
  statement content, stack traces or SQL in `error.message` / `details`. Errors do not reveal whether a phone number
  is registered beyond what the flow already shows.
- Untrusted input: every request field has bounds — length, range, `pattern`, array size, file size and type
  (413 / 422); free text (`q`, assistant questions) has a maximum; pagination has a maximum page size; nothing in
  the contract lets a client pick a server-side path, URL, SQL fragment or prompt template.
- Abuse and cost: a new endpoint that sends a call or SMS, runs the LLM or parses a file states its server-side rate
  limit or quota (`RATE_LIMITED`, `LIMIT_EXCEEDED`) and requires `Idempotency-Key`, so a retry does not spend twice.
  Existing endpoints without a stated limit (statement upload 3.2 today) are a known gap: a finding only when the
  PR changes their cost or exposure, otherwise a separate issue.
- Consent: data goes to the LLM assistant only after the assistant consent; an account is created only after the
  personal data consent; revoking a consent and deleting the account state what happens to stored data and in what
  time.
- Trust boundaries in gRPC: each metadata key states where it comes from and how far it is trusted
  (`docs/grpc-api.md`, «Metadata»). `x-client-ip` is computed by `bank-bff` from the TCP peer, or, only when that peer
  is in the explicitly configured `TRUSTED_PROXIES` CIDRs, from `X-Forwarded-For` appended by controlled proxies:
  walk all header lines from right to left to the first untrusted address, never trust its client-supplied prefix.
  Missing forwarding data keeps the TCP peer; malformed trusted suffixes or more than 16 examined elements keep
  the last trusted hop. A mandatory condition for every trusted CIDR is that it contains only controlled HTTP
  proxies that themselves append their TCP peer IP to `X-Forwarded-For`. Never include L4 balancers, NAT/SNAT,
  non-appending hops, client networks or all addresses: skipping such a hop can expose a spoofed client prefix.
  If L4/NAT hides the client IP, recover it at the first HTTP proxy from a trusted infrastructure source
  (e.g. PROXY protocol / `real_ip`), never by expanding the trusted list. Services
  accept this metadata only from `bank-bff`. Never copy HTTP `X-Client-IP`;
  `x-client-version` is copied from a client header — spoofable, used for logs, metrics and
  the minimum supported version, never for authorization; a new method gets its row in the caller table; `PERMISSION_DENIED` to a foreign caller
  carries no details.
- Client-visible limits are informational: the contract never relies on the client to enforce a limit, a quota or a
  permission.

### Extensibility

The decision of 13.10.2026 («Расширяемость» and «Нотация» in `docs/api.md`): the contract grows by addition, and
`deprecated` is an exception. For each added or changed field, code, block or method check:

- A new response field is optional, and an old client that ignores it still behaves correctly. A new required
  request field, a removed or renamed field, a changed type or meaning is breaking: it needs `/v2` or, in the alpha
  and for REST or a changed `error_spec` of an existing code only, the owner's `breaking-accepted`, and the PR says
  so. Any other breaking `.proto` change is not accepted (CI).
- A set that can grow is a `code` (`x-extensible-enum` in OpenAPI, documented behaviour for an unknown value), not an
  `enum`. A closed `enum` in a response is a finding unless the set truly cannot grow.
- A code the client turns into text comes with a server text (`*_name`, `text`) for unknown values; a value of
  varying nature carries `value_kind`; amounts carry `currency`, as integers in minor units (no JSON floats for
  money or `decimal`).
- What will grow in composition or order (screens, metrics, blocks) is decided by the server as a list of catalog
  blocks, not as a tree of named fields; a new block type extends the catalog and `supports`.
- A growing set of entities is a list with a key (`consents[]` with `type`), not a field per entity; no field name
  is reused with another shape in another block type.
- `deprecated` appears only together with its replacement, links a FIN issue and states the removal condition
  («Удаление устаревшего»).
- Proto: field numbers and names are never reused (`reserved` for removed ones); enums keep an `UNSPECIFIED` zero
  value; new fields do not change the meaning of old ones.
- Lists that can grow have pagination or a stated bound; a filter or parameter can be added later without changing
  existing responses.

### Clarity and consistency

- The meaning of every new or changed field is unambiguous from `docs/api.md` alone: type, unit, time zone, inclusive
  or exclusive bounds, when absent or `null`, who computes it (server or client). A reader should not have to guess
  from the name. The criterion is a missing or ambiguous fact, not the quality of the phrasing: a clumsy but
  unambiguous sentence is not a finding.
- Names follow the existing ones (`snake_case`, the same word for the same thing across endpoints, `*_name` for
  display texts, `from` / `to` half-open ranges); no synonyms for an existing concept.
- `docs/api.md`, `openapi.yaml`, `docs/grpc-api.md` and `proto/` agree: same fields, types, optionality, codes and
  limits. The error registry matches section 8 (CI checks part of it; check the rest).
- HTTP semantics: the method and status match the general rules (error order, `409` only for idempotency, `422` for
  understood but impossible); one `error` object; idempotency of the method is stated.
- Examples in `docs/api.md` and `openapi.yaml` are valid against the schema and show the non-obvious case.

### Checklist

- Every new `POST` with a side effect requires `Idempotency-Key`.
- Every new error code is in section 8 and `openapi.yaml`, with status and client behaviour; service codes also
  enter `ErrorReason`. BFF-only transport exceptions are documented in grpc-api.md5.1 and the registry checker.
- Every new `code` documents the behaviour for an unknown value; no new closed `enum` in a response for a set
  that can grow.
- Every new endpoint documents auth, consent, limits and errors; every new gRPC method its callers and call model.
- A contract change names its client issue.

## Renovate automation

FIN-211: dependency-only Renovate PRs may be merged/closed by the reviewed `renovate-policy.yml` controller, after exact-head required CI and bot-only commit checks. Ordinary PRs and release-to-main PRs still require the owner. Do not enable native automerge or bypass protections. Preserve base policy in `renovate-base.json` (schedules, groups, allowed versions and manual Go upgrades); generated target/version approvals live on the separate `renovate-policy` branch. Failed exact values are isolated for explicit Dashboard approval; new values keep their original groups. AndroidX/Compose and mixed groups never auto-merge. See `docs/renovate-policy.md` for credentials, safeguards, bootstrap and verification.
