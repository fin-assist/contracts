# Контракты «Понятных финансов»

Источник правды для API приложения «Понятные финансы». Агентам — сначала `AGENTS.md` (карта репозитория, правила контракта и ревью; `CLAUDE.md` импортирует его).

- **Клиентский REST API** (мобильный клиент ↔ `bank-bff`): `docs/api.md` — спецификация и смысл полей, `docs/calc.md` — как они считаются (правила учёта и метрики), `openapi/openapi.yaml` — машиночитаемый контракт OpenAPI 3.0.3 (кодогенерация, валидация kin-openapi, oasdiff).
- **Внутренний gRPC** между Go-сервисами: `proto/` — `.proto` (`buf`), `docs/grpc-api.md` — как сервисы делят работу, ошибки, идемпотентность, outbox.
- **Go-модуль** `github.com/fin-assist/contracts/gen/go` — код, сгенерированный из `proto/`, и пакет ошибок `finerr`; его подключают все сервисы и библиотеки бэкенда (FIN-116).

Confluence PF (https://finassist.atlassian.net/wiki/spaces/PF) — только выжимка и ссылка сюда. Правится здесь.

Формат пользовательских ошибок FIN-224 добавляет `user_message` / `severity` и `error_user_message` / `error_severity`: поля необязательны, но не принимают `null` (`optional<T>` в `docs/api.md`). Сервер опускает отсутствующие значения, в том числе оба новых поля истории у сообщений не `failed`. Прежние nullable-поля не меняются; отсутствие канонического текста и некорректный `null` обрабатываются по разным правилам.

В REST `Error.retryable` (`optional<bool>`) отдельно выражает две гарантии вместе: безопасность и разрешение повторно отправить этот конкретный запрос с прежним телом и тем же ключом, пока действуют ограничения метода и окно ключа. Корректный `false` запрещает прямой повтор, включая автоматическое переоткрытие SSE после REST-отказа, и не отменяет корректирующие сценарии; отсутствующий или некорректный флаг не даёт новых разрешений и сохраняет прежнее безопасное поведение. Это не доменный `retryable` помощника в SSE/истории: там разрешается `RetryAnswer`, новая генерация с новым ключом. Каталог представления принадлежит BFF, permission исходного RPC — сервису/usecase в optional `common.v1.RequestRetryInfo`; BFF соединяет его с доказанной safety всего REST-запроса, без business-switch по причинам. Общая внутренняя ошибка — `google.rpc.Status` со стандартными details; nested Status в terminal/history не меняет успешный транспорт чтения и не содержит request-permission или относительного request-delay. Новая попытка остаётся отдельным свойством результата. Совместимость/dual-write/реальная оговорка replay телефонного429 — `docs/grpc-api.md`5.4–5.5.

FIN-224 теперь добавляет protobuf/API: `RequestRetryInfo`, вложенные Status (`AnswerError.status`6, `Message.error`14, `ImportStreamError.status`3), `finerr.WithRequestRetryInfo`, `NewResultStatus`, `DecodeResultStatus` и `REST.RequestRetryable`. После merge владелец выпускает новый неизменяемый `gen/go` тег до зависимых backend/library PR. Старые поля/constructor/error_spec и внешняя JSON-форма не переопределяются. `ErrorEvent` — нейтральное имя общей внешней формы события, `SseError` сохраняется совместимым алиасом. Runtime/deploy/мониторинг — FIN-225/FIN-223; FIN-225 ждёт отдельную FIN-268.

Время ожидания: свежие `Retry-After`/`expires_in` плюс монотонное elapsed time с учётом сна; BFF даёт свежий `Date`, включая ошибки. Fallback абсолютного datetime через Date того же ответа — приблизительный UI, не proof токена/ключа/permission. Потеря базы требует обновления состояния, не перезапуска старой duration (`docs/api.md`, «Время ожидания и часы»).

FIN-339 задаёт минимальный транспортный бюджет JSON 65 536 байт для шести операций FIN-268 и BFF-only `REQUEST_TOO_LARGE` (413); область и порядок — `docs/api.md`, «Размер JSON-запроса». У восьми операций без тела фактические 0 байт допустимы независимо от заголовков, от 1 байта — 400. У шести JSON-операций сжатие не принимается (400 без распаковки), прежние защиты остальных операций сохраняются. `FILE_TOO_LARGE`, protobuf, все service `error_spec` и Go API не меняются; новый Go-тег для этого инкремента не нужен.

## Автоматизация обновлений зависимостей

[Политика Renovate](docs/renovate-policy.md) (FIN-211): одна актуальная release/main, автомерж полностью ботских PR после обязательного CI, один повтор красного CI и закрытие с явным повтором через Dependency Dashboard. AndroidX/Compose остаются на ручной проверке. Исходные группировки, ограничения версий и расписания — `renovate-base.json`; generated target и quarantine — ветка `renovate-policy`. Активация требует отдельного токена и строгой защиты веток; по умолчанию выключена.

## Раскладка

| Путь | Что |
| --- | --- |
| `docs/api.md` | Клиентский API (REST + SSE), реестр ошибок — раздел 8 |
| `docs/calc.md` | Расчёт метрик и правила учёта (FIN-106, FIN-158): пары, возвраты, покрытие, метрики «Аналитики», порядок и состав блоков |
| `openapi/openapi.yaml` | OpenAPI 3.0.3 того же API |
| `docs/grpc-api.md` | Внутренний gRPC API |
| `proto/buf.yaml`, `proto/buf.lock` | Модуль `buf`: lint STANDARD; зависимость `buf.build/bufbuild/protovalidate` для аннотаций проверки полей (`docs/grpc-api.md`, раздел 8) — тот же коммит, из которого собран Go-модуль protovalidate у сервисов (FIN-202); googleapis BSR-модуль закреплён для стандартного google.rpc.Status |
| `proto/finassist/<сервис>/v1/*.proto` | Контракты сервисов и `common/v1` (типы, реестр ошибок `ErrorReason`) |
| `proto/scripts/check_error_registry.py` | Сверка реестра ошибок с разделом 8 `docs/api.md` |
| `proto/scripts/check_error_spec_stable.py` | `error_spec` существующих кодов не меняется относительно базы PR |
| `proto/buf.gen.yaml` | Генерация Go-кода: `protoc-gen-go`, `protoc-gen-go-grpc`, `protoc-gen-finerr` |
| `gen/go/` | Go-модуль `github.com/fin-assist/contracts/gen/go` (свой `go.mod`): `finassist/<сервис>/v1` — сообщения и gRPC, `finerr` — конструкторы unary-ошибок/HTTP mapping, transport-only request advice и generic result Status helpers. Сгенерирован, руками не правится (кроме `go.mod` и тестов) |
| `tools/` | Модуль инструментов: версии `buf`, `protoc-gen-go`, `protoc-gen-go-grpc` закреплены в `tools/go.mod` (`tool`); `tools/protoc-gen-finerr` — генератор `finerr` из опций `error_spec` |
| `fixtures/requests/body-policy.json` | HTTP-примеры пустого/неожиданного тела и Content-Encoding FIN-339 для контрактных и BFF-тестов FIN-268 |
| `tools/openapicheck/` | Go-тесты совместимости OpenAPI с фактическим `kin-openapi` (версия в `tools/go.mod`): валидация полного документа включая встроенные примеры, шесть JSON-бюджетов/ответов 413 FIN-339, те же примеры `fixtures/errors`, Unicode-границы и необязательные non-null поля. Зависимость только для тестов tools; Go API и runtime `gen/go` не меняются |
| `scripts/generate.sh` | Перегенерировать `gen/go` закреплёнными версиями |
| `fixtures/calc/` | Эталонные данные расчёта (FIN-107): выписки, сценарий, ожидаемые ответы 3.3, 4.1, 4.3, 5.1, 6.1, 6.2 и правила сравнения — `fixtures/calc/README.md`. По ним тестируются фейковый бэкенд и Go-бэкенд. `categories.json` — ещё и список известных категорий банка, который берут сервисы (`docs/calc.md` 3.1, FIN-200) |
| `fixtures/errors/presentation.json`, `scripts/test_error_presentation.py` | Примеры совместимого формата ошибок FIN-224 и проверки OpenAPI: неизвестный код/уровень, прежние REST/SSE/история, границы Unicode-текста, тип REST `retryable`, совпадающий reason в разных контекстах и фиксированные внешние проекции present-invalid Status. Поведение клиента реализуется в FIN-223, политика BFF — FIN-225 |
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
| Примеры ошибок `fixtures/errors` (в job `calc-fixtures`) | всегда | прежний ответ перестал проходить схему; неизвестный открытый код/уровень не принимается; нарушены ограничения пользовательского текста; ошибки схемы при некорректном необязательном поле не локальны для этого поля. Сохранение кодов и метаданных при клиентском разборе проверяется в FIN-223 |
| Go-модуль `gen/go` и тесты tools | всегда | `gen/go` не совпадает с тем, что генерирует `scripts/generate.sh` из `proto/`; модуль не собирается, `go vet`, race-тесты модулей `gen/go` / `tools` (включая фактический kin-openapi в `tools/openapicheck`) или тесты отчёта покрытия падают; версия `buf` в `tools/go.mod` расходится с `BUF_VERSION` в `ci.yml` |

Локально:

```bash
# OpenAPI lint
npx @redocly/cli@1 lint openapi/openapi.yaml

# Protobuf lint
cd proto && buf lint

# Error registry check
cd proto && buf build -o /tmp/fd.binpb && python3 scripts/check_error_registry.py /tmp/fd.binpb ../docs/api.md

# Error presentation examples and schema boundaries (same Python dependencies as calculation fixtures)
python3 -m unittest discover -s scripts -p 'test_error_presentation.py'

# Actual Go OpenAPI validator compatibility (also included in the tools race tests below)
(cd tools && go test -race -count=1 -timeout=2m ./openapicheck)

# Calculation fixtures: reference calculation tests, then regenerate fixtures/calc/expected and check the schema
cd tools/refcalc && python3 -m unittest discover -s tests -t . && python3 -m refcalc.scenario ../../fixtures/calc --write --openapi ../../openapi/openapi.yaml

# Regenerate gen/go (pinned buf and plugins are built into .bin/), then build and test it
scripts/generate.sh
(cd gen/go && go build ./... && go vet ./... && go test -race -count=1 -coverpkg=./... -coverprofile=cover.out ./...)
(cd tools && go test -race -count=1 -coverpkg=./... -coverprofile=cover.out ./...)
python3 -m unittest discover -s scripts -p 'test_coverage_diff.py'
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

### Общее покрытие и изменённые строки

CI публикует разделы «Покрытие проекта» и «Покрытие изменённых строк» в сводке запуска и одном обновляемом комментарии PR. Оба раздела используют один профиль на Go-модуль, без повторного запуска тестов; на push сохраняется общее покрытие. Комментарий для PR из fork не записывается. При ошибке построения или пустом файле отчёта последний корректный комментарий сохраняется.

Метрика — уникальные строки внутри блоков Go-профиля с ненулевым числом statements, а не процент statements из `go tool cover`. `_test.go` и `testdata/` исключены; сгенерированный production-код включён на тех же условиях. Повторные блоки разных тестовых бинарей объединяются: достаточно одного выполнения. Без исполняемых строк отчёт сообщает «Нет данных», а не 100 %. Оба процента информационные: этот PR не вводит в репозитории новую цель покрытия или порог для сгенерированного кода.

В `contracts` итог суммирует `gen/go` (включая сгенерированные сообщения и `finerr`) и `tools` (генератор). Пути в таблице относительно корня репозитория, модули не смешиваются:

```bash
(cd gen/go && go test -race -count=1 -coverpkg=./... -coverprofile=cover.out ./...)
(cd tools && go test -race -count=1 -coverpkg=./... -coverprofile=cover.out ./...)
python3 scripts/coverage-diff.py --module-profile gen/go gen/go/cover.out --module-profile tools tools/cover.out --base origin/main
```
