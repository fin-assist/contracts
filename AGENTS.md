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
  `docs/grpc-api.md`, `proto/`. Confluence PF (https://finassist.atlassian.net/wiki/spaces/PF) holds only a summary
  and a link here; MVP scope, architecture and product decisions live there («Состав MVP», «Архитектура»,
  «Продуктовые решения»).
- Version 0.2 is crystallized: every change goes through a YouTrack issue (`FIN-<n>`, finassist.youtrack.cloud) and
  is referenced in the PR.
- A contract change is two issues: one for the contract (this repo) and one for the client (fin-assist/android,
  including its fake backend `:mock:backend`); the client issue depends on the contract issue.
- Mismatches between mockups, docs and the contract are expected. Resolve them in favour of `docs/api.md` + the
  latest decision, say so in the PR, propose (not impose) changes.
- There is no backend yet: the Android `mock` flavor implements this contract in-process. The backend (Go,
  PostgreSQL) will be written against it; `go_package` points to the future `fin-assist/backend`.

## Repository map

| What | Where |
|---|---|
| General rules of the REST API: format, money, IDs, notation (`enum` / `code` / text), auth, headers, idempotency, errors, SSE, extensibility | `docs/api.md`, «Общие правила» |
| Block catalog shared by Analytics and the assistant | `docs/api.md`, «Блоки» |
| Endpoints | `docs/api.md`, sections 1–7 (`/v1/auth`, `/v1/profile`, `/v1/statements`, `/v1/operations`, `/v1/categories`, `/v1/analytics`, `/v1/assistant`) |
| Error registry | `docs/api.md`, section 8; `proto/finassist/common/v1/errors.proto` (`ErrorReason`), `error_options.proto` |
| Extensibility decisions | `docs/api.md`, «Расширяемость» and «Решения по расширяемости» |
| OpenAPI 3.0.3 of the same API | `openapi/openapi.yaml`; lint config `redocly.yaml` |
| Services, call models (user / system), service identity, metadata, errors, idempotency, outbox | `docs/grpc-api.md` |
| Service contracts | `proto/finassist/<service>/v1/*.proto`, shared types `proto/finassist/common/v1/` |
| Registry check | `proto/scripts/check_error_registry.py` |
| PR review by Claude | `.github/workflows/claude.yml` |

## Checks

CI (`.github/workflows/ci.yml`, table in `README.md`): `redocly lint`, `oasdiff breaking` against the PR base,
`buf lint`, `buf breaking`, `check_error_registry.py`. Run them locally before pushing (commands in `README.md`,
«CI»).

A breaking change fails `oasdiff` / `buf breaking`. In the alpha the owner may accept it with the PR label
`breaking-accepted` (REST); never add that label yourself — ask in the PR.

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
  and for REST only, the owner's `breaking-accepted`; `buf breaking` does not read labels, so a breaking `.proto`
  change goes through `reserved` and a new field or method instead.
- Errors: a new code goes into section 8 of `docs/api.md` and `ErrorReason` together, with HTTP status, `error_spec`
  and client behaviour; removed numbers and names go to `reserved`.
- Idempotency: every new `POST` that creates a resource or has a side effect requires `Idempotency-Key`; say which
  responses are stored («Идемпотентность»).
- Every new endpoint states who may call it (token or not, which consent), its limits (size, count, rate) and its
  errors.

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

  - `@claude` — `.github/workflows/claude.yml` (runs from the default branch), Claude Fable 5.1 (`claude-fable-5-1`,
    set in `claude_args`). Needs the Claude GitHub App and the `CLAUDE_CODE_OAUTH_TOKEN` secret.
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
  (`docs/grpc-api.md`, «Metadata»). `x-client-ip` is computed by `bank-bff` from the TCP connection and never taken
  from client headers; `x-client-version` is copied from a client header — spoofable, used for logs, metrics and
  the minimum supported version, never for authorization; a new method gets its row in the caller table; `PERMISSION_DENIED` to a foreign caller
  carries no details.
- Client-visible limits are informational: the contract never relies on the client to enforce a limit, a quota or a
  permission.

### Extensibility

The decision of 13.10.2026 («Расширяемость» and «Нотация» in `docs/api.md`): the contract grows by addition, and
`deprecated` is an exception. For each added or changed field, code, block or method check:

- A new response field is optional, and an old client that ignores it still behaves correctly. A new required
  request field, a removed or renamed field, a changed type or meaning is breaking: it needs `/v2` or, in the alpha
  and for REST only, the owner's `breaking-accepted`, and the PR says so. In `.proto` it is not accepted (CI).
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
- Every new error code is in section 8, `ErrorReason` and `openapi.yaml`, with status and client behaviour.
- Every new `code` documents the behaviour for an unknown value; no new closed `enum` in a response for a set
  that can grow.
- Every new endpoint documents auth, consent, limits and errors; every new gRPC method its callers and call model.
- A contract change names its client issue.
