# Контракты «Понятных финансов»

Источник правды для API приложения «Понятные финансы»:

- **Клиентский REST API** (мобильный клиент ↔ `bank-bff`): `docs/api.md` — спецификация и смысл полей, `openapi/openapi.yaml` — машиночитаемый контракт OpenAPI 3.0.3 (кодогенерация, валидация kin-openapi, oasdiff).
- **Внутренний gRPC** между Go-сервисами: `proto/` — `.proto` (`buf`), `docs/grpc-api.md` — как сервисы делят работу, ошибки, идемпотентность, outbox.

Confluence PF (https://finassist.atlassian.net/wiki/spaces/PF) — только выжимка и ссылка сюда. Правится здесь.

## Раскладка

| Путь | Что |
| --- | --- |
| `docs/api.md` | Клиентский API (REST + SSE), реестр ошибок — раздел 8 |
| `openapi/openapi.yaml` | OpenAPI 3.0.3 того же API |
| `docs/grpc-api.md` | Внутренний gRPC API |
| `proto/buf.yaml` | Модуль `buf`: lint STANDARD |
| `proto/finassist/<сервис>/v1/*.proto` | Контракты сервисов и `common/v1` (типы, реестр ошибок `ErrorReason`) |
| `proto/scripts/check_error_registry.py` | Сверка реестра ошибок с разделом 8 `docs/api.md` |
| `redocly.yaml` | Настройки линтера OpenAPI |

Ссылки внутри файлов перенесены без правок и написаны относительно прежнего расположения: `api.md` и `grpc-api.md` → `docs/`, `openapi.yaml` → `openapi/`, «`proto/finassist/**/v1` (рядом с этим файлом)» → `proto/` в корне, `scripts/check_error_registry.py` → `proto/scripts/`. Упоминания `architecture.md`, `storage.md`, `mvp-scope.md`, `decision-ofx-only.md` и других — документы проекта, в этот репозиторий не входят. Путь `go_package` (`github.com/fin-assist/backend/gen/go/...`) — будущий бэкенд-репозиторий.

## Правила

- **Приоритет.** При расхождении смысл полей задаёт `docs/api.md`; `openapi.yaml` правится вслед.
- **Только через задачу.** Любое изменение — через задачу YouTrack `FIN-<n>` (https://finassist.youtrack.cloud), ссылка на неё в PR.
- **Изменение контракта — две задачи:** на контракт (этот репозиторий) и на клиент; клиентская зависит от контрактной.
- **Версия 0.2 кристаллизована.** Правки — только инкрементом через задачу.
- **Расширяемость** (раздел «Расширяемость» в `docs/api.md`): новое — добавлением; открытые наборы — `x-extensible-enum`, клиент обрабатывает неизвестное значение; `enum` — только для закрытых наборов; `deprecated` — только в исключительных случаях. Несовместимое изменение — новая версия API.
- **Реестр ошибок** меняется только вместе с разделом 8 `docs/api.md` (`docs/grpc-api.md`, 5.1); удалённые номера и имена — в `reserved`.

## CI

`.github/workflows/ci.yml`, на push и pull request:

| Проверка | Когда | Что не пропустит |
| --- | --- | --- |
| `redocly lint openapi/openapi.yaml` | всегда | невалидный OpenAPI, нарушения правил `recommended` (кроме отключённых в `redocly.yaml`) |
| `oasdiff breaking` (база PR → PR) | PR | несовместимые изменения REST (уровень `ERR`). В альфе владелец принимает их меткой PR `breaking-accepted` — тогда изменения перечисляются, но проверка не падает |
| `buf lint` | всегда | нарушения STANDARD (исключение — префикс значений `ErrorReason`) |
| `buf breaking` против базы PR | PR | несовместимые изменения `.proto`, повторное использование номеров и имён |
| `check_error_registry.py` | всегда | расхождение `ErrorReason` с разделом 8 `docs/api.md`, неполный `error_spec`, `error_spec` вне реестра, больше 30 кодов |

Локально:

```bash
# OpenAPI lint
npx @redocly/cli@1 lint openapi/openapi.yaml

# Protobuf lint
cd proto && buf lint

# Error registry check
cd proto && buf build -o /tmp/fd.binpb && python3 scripts/check_error_registry.py /tmp/fd.binpb ../docs/api.md
```

## Ревью Claude

`.github/workflows/claude.yml` (FIN-97): упоминание `@claude` в комментарии или ревью PR запускает Claude Code — ревью с замечаниями inline на изменённых строках. Нужны GitHub-приложение Claude на репозитории и секрет `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`). Workflow по комментариям запускается из `main`, поэтому правки в нём действуют после мержа.
