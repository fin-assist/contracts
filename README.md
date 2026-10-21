# Контракты «Понятных финансов»

Источник правды для API приложения «Понятные финансы». Агентам — сначала `AGENTS.md` (карта репозитория, правила контракта и ревью; `CLAUDE.md` импортирует его).

- **Клиентский REST API** (мобильный клиент ↔ `bank-bff`): `docs/api.md` — спецификация и смысл полей, `docs/calc.md` — как они считаются (правила учёта и метрики), `openapi/openapi.yaml` — машиночитаемый контракт OpenAPI 3.0.3 (кодогенерация, валидация kin-openapi, oasdiff).
- **Внутренний gRPC** между Go-сервисами: `proto/` — `.proto` (`buf`), `docs/grpc-api.md` — как сервисы делят работу, ошибки, идемпотентность, outbox.
- **Go-модуль** `github.com/fin-assist/contracts/gen/go` — код, сгенерированный из `proto/`, и пакет ошибок `finerr`; его подключают все сервисы и библиотеки бэкенда (FIN-116).

Confluence PF (https://finassist.atlassian.net/wiki/spaces/PF) — только выжимка и ссылка сюда. Правится здесь.

## Раскладка

| Путь | Что |
| --- | --- |
| `docs/api.md` | Клиентский API (REST + SSE), реестр ошибок — раздел 8 |
| `docs/calc.md` | Расчёт метрик и правила учёта (FIN-106, FIN-158): пары, возвраты, покрытие, метрики «Аналитики», порядок и состав блоков |
| `openapi/openapi.yaml` | OpenAPI 3.0.3 того же API |
| `docs/grpc-api.md` | Внутренний gRPC API |
| `proto/buf.yaml`, `proto/buf.lock` | Модуль `buf`: lint STANDARD; зависимость `buf.build/bufbuild/protovalidate` для аннотаций проверки полей (`docs/grpc-api.md`, раздел 8) — тот же коммит, из которого собран Go-модуль protovalidate у сервисов (FIN-202) |
| `proto/finassist/<сервис>/v1/*.proto` | Контракты сервисов и `common/v1` (типы, реестр ошибок `ErrorReason`) |
| `proto/scripts/check_error_registry.py` | Сверка реестра ошибок с разделом 8 `docs/api.md` |
| `proto/scripts/check_error_spec_stable.py` | `error_spec` существующих кодов не меняется относительно базы PR |
| `proto/buf.gen.yaml` | Генерация Go-кода: `protoc-gen-go`, `protoc-gen-go-grpc`, `protoc-gen-finerr` |
| `gen/go/` | Go-модуль `github.com/fin-assist/contracts/gen/go` (свой `go.mod`): `finassist/<сервис>/v1` — сообщения и gRPC, `finerr` — конструкторы ошибок и таблица перевода в REST. Сгенерирован, руками не правится (кроме `go.mod` и тестов) |
| `tools/` | Модуль инструментов: версии `buf`, `protoc-gen-go`, `protoc-gen-go-grpc` закреплены в `tools/go.mod` (`tool`); `tools/protoc-gen-finerr` — генератор `finerr` из опций `error_spec` |
| `scripts/generate.sh` | Перегенерировать `gen/go` закреплёнными версиями |
| `fixtures/calc/` | Эталонные данные расчёта (FIN-107): выписки, сценарий, ожидаемые ответы 3.3, 4.1, 4.3, 5.1, 6.1, 6.2 и правила сравнения — `fixtures/calc/README.md`. По ним тестируются фейковый бэкенд и Go-бэкенд. `categories.json` — ещё и список известных категорий банка, который берут сервисы (`docs/calc.md` 3.1, FIN-200) |
| `tools/refcalc/` | Референсный расчёт на Python по `docs/calc.md`: считает ожидаемые ответы `fixtures/calc/expected`, тесты — примеры раздела 9 |
| `redocly.yaml` | Настройки линтера OpenAPI |
| `AGENTS.md`, `CLAUDE.md` | Инструкции агентам: карта, правила контракта, процесс, `## Code Review Rules` |

Ссылки внутри файлов перенесены без правок и написаны относительно прежнего расположения: `api.md` и `grpc-api.md` → `docs/`, `openapi.yaml` → `openapi/`, «`proto/finassist/**/v1` (рядом с этим файлом)» → `proto/` в корне, `scripts/check_error_registry.py` → `proto/scripts/`. Упоминания `architecture.md`, `storage.md`, `data-sources.md`, `mvp-scope.md`, `decision-ofx-only.md` и других — бывшие документы claude.ai-проекта, в этот репозиторий не входят; теперь это страницы Confluence PF: [«Архитектура»](https://finassist.atlassian.net/wiki/spaces/PF/pages/5210132), [«Хранение данных»](https://finassist.atlassian.net/wiki/spaces/PF/pages/11239425), [«Источники данных»](https://finassist.atlassian.net/wiki/spaces/PF/pages/11141138), [«Состав MVP»](https://finassist.atlassian.net/wiki/spaces/PF/pages/5242881), [«Решение: импорт только в OFX»](https://finassist.atlassian.net/wiki/spaces/PF/pages/11206657); остальные — в [разделе PF](https://finassist.atlassian.net/wiki/spaces/PF). Путь `go_package` — `github.com/fin-assist/contracts/gen/go/...` (модуль в этом репозитории, FIN-116).

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
| `check_error_spec_stable.py` против базы PR | PR | изменение `error_spec` (`grpc_code`, `http_status`, `metadata_keys`, `details`) у существующего кода. Владелец принимает его меткой `breaking-accepted` — изменения перечисляются, проверка не падает |
| Эталонные данные `fixtures/calc` | всегда | тесты референсного расчёта (примеры `docs/calc.md`) падают; `expected/` не совпадает с перегенерированным сценарием; ответ (или `data` блока) не проходит схему `openapi.yaml`; поле схемы ответов не встречается ни в одном ожидаемом ответе и не внесено в `coverage-exceptions.json` |
| Go-модуль `gen/go` | всегда | `gen/go` не совпадает с тем, что генерирует `scripts/generate.sh` из `proto/`; модуль не собирается, `go vet` или тесты `finerr` падают; версия `buf` в `tools/go.mod` расходится с `BUF_VERSION` в `ci.yml` |

Локально:

```bash
# OpenAPI lint
npx @redocly/cli@1 lint openapi/openapi.yaml

# Protobuf lint
cd proto && buf lint

# Error registry check
cd proto && buf build -o /tmp/fd.binpb && python3 scripts/check_error_registry.py /tmp/fd.binpb ../docs/api.md

# Calculation fixtures: reference calculation tests, then regenerate fixtures/calc/expected and check the schema
cd tools/refcalc && python3 -m unittest discover -s tests -t . && python3 -m refcalc.scenario ../../fixtures/calc --write --openapi ../../openapi/openapi.yaml

# Regenerate gen/go (pinned buf and plugins are built into .bin/), then build and test it
scripts/generate.sh
cd gen/go && go build ./... && go vet ./... && go test ./...
```

## Go-модуль `gen/go`

Сервисы подключают одну версию контрактов:

```bash
go get github.com/fin-assist/contracts/gen/go@v0.1.0
```

```go
import (
	statementdatav1 "github.com/fin-assist/contracts/gen/go/finassist/statementdata/v1"
	"github.com/fin-assist/contracts/gen/go/finerr"
)

return nil, finerr.LimitExceeded(resetsAt.In(moscow)) // *_at keys are RFC 3339 moments; the Location sets the offset the client sees (resets_at: +03:00, api.md 7.5)
```

- **Изменение `.proto`** — в том же PR `scripts/generate.sh` и коммит `gen/go`; CI сверяет. Новый импорт в сгенерированном коде (ещё один well-known type) — `go get` в `gen/go`: `go.mod` модуля скрипт не трогает.
- **Версии.** Модуль во вложенном каталоге, поэтому тег — `gen/go/vX.Y.Z` (так его находит `go get`). Пока альфа — `v0.Y.Z`: совместимое изменение — новая минорная (`Y`) или патч-версия (`Z`, только исправления описаний и генератора). Несовместимое изменение `.proto` (его не пропускает `buf breaking`) — только по решению владельца; после `v1` — новая major-версия с путём `gen/go/v2`.
- **Выпуск** — после мержа в `main` владелец ставит тег на коммит мержа и пушит его:

  ```bash
  git tag gen/go/v0.1.0 <коммит мержа> && git push origin gen/go/v0.1.0
  ```

  Затем сервисы поднимают версию через `go get`. Тег не переставляется: опубликованную версию кэширует `proxy.golang.org`.
- **`error_spec` существующего кода не меняется** (ключи `metadata_keys`, `details`, `grpc_code`, `http_status`): `buf breaking` опций не сравнивает, а изменение ломает сигнатуру конструктора `finerr` и при разных версиях модуля у сервиса и `bank-bff` превращает ошибку в 500. Нужен другой ответ клиенту — новый код по правилам `docs/grpc-api.md` 5.1. CI сверяет `error_spec` с базой PR (`check_error_spec_stable.py`); изменение принимает только владелец меткой `breaking-accepted`. Если без изменения не обойтись (решение владельца), последней выкатывается сторона, которая начинает требовать больше: при добавлении ключа или детали — сначала все сервисы, потом `bank-bff` (старый `bank-bff` лишнее отбрасывает, а новый без ключа отдаст 500); при удалении — сначала `bank-bff`, потом сервисы. Смена `http_status`/`grpc_code` порядка не требует.

## Ревью

Ревью PR — два ревьюера по разделу `## Code Review Rules` в `AGENTS.md`, с отдельными проходами «Безопасность» и «Расширяемость». Запрос — один комментарий в PR, начинающийся с `@codex @claude` (пример — `AGENTS.md`, Workflow).

- `.github/workflows/claude.yml` (FIN-97): упоминание `@claude` запускает Claude Code на Claude Opus 5.5 (`claude-opus-5-5`, `--effort high`; FIN-204) — ревью с замечаниями inline на изменённых строках. Нужны GitHub-приложение Claude на репозитории и секрет `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`). Workflow по комментариям запускается из `main`, поэтому правки в нём действуют после мержа.
- `@codex` — интеграция Codex на GitHub по подписке ChatGPT владельца; модель выбирает OpenAI, из репозитория она не задаётся (FIN-103).
