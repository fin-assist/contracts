package openapicheck_test

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/getkin/kin-openapi/openapi3"
)

func loadContract(t *testing.T) *openapi3.T {
	t.Helper()
	doc, err := openapi3.NewLoader().LoadFromFile(filepath.Join("..", "..", "openapi", "openapi.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	return doc
}

func schema(t *testing.T, doc *openapi3.T, name string) *openapi3.Schema {
	t.Helper()
	ref, ok := doc.Components.Schemas[name]
	if !ok || ref.Value == nil {
		t.Fatalf("schema %q is missing or unresolved", name)
	}
	return ref.Value
}

func TestContractValidatesWithKinOpenAPI(t *testing.T) {
	doc := loadContract(t)
	// Two unrelated response examples on the base branch omit required currency/changed_since_text fields.
	// Skip only embedded example validation; patterns, schemas and the rest of the full document are validated.
	// Every new error fixture is validated separately through VisitJSON below, without this exception.
	if err := doc.Validate(context.Background(), openapi3.DisableExamplesValidation()); err != nil {
		t.Fatal(err)
	}
}

func TestErrorPresentationFixturesWithKinOpenAPI(t *testing.T) {
	doc := loadContract(t)
	data, err := os.ReadFile(filepath.Join("..", "..", "fixtures", "errors", "presentation.json"))
	if err != nil {
		t.Fatal(err)
	}
	var examples []struct {
		Name   string `json:"name"`
		Schema string `json:"schema"`
		Value  any    `json:"value"`
	}
	if err := json.Unmarshal(data, &examples); err != nil {
		t.Fatal(err)
	}
	if len(examples) == 0 {
		t.Fatal("no error contract examples")
	}
	for _, example := range examples {
		t.Run(example.Name, func(t *testing.T) {
			if err := schema(t, doc, example.Schema).VisitJSON(example.Value); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestUserMessageBoundariesWithKinOpenAPI(t *testing.T) {
	text := schema(t, loadContract(t), "UserMessage")
	for _, value := range []string{"Я", strings.Repeat("🪙", 1000), "\u200b", "\u00a0Текст\u3000"} {
		if err := text.VisitJSON(value); err != nil {
			t.Errorf("valid text with %d code points rejected: %v", len([]rune(value)), err)
		}
	}
	for _, value := range []any{"", strings.Repeat("я", 1001), strings.Repeat("🪙", 1001), nil, 1, []any{}, map[string]any{}} {
		if err := text.VisitJSON(value); err == nil {
			t.Errorf("invalid text of type %T accepted", value)
		}
	}
	// The exact Unicode White_Space set from docs/api.md; do not use a language-specific trim predicate.
	whitespace := []rune{0x0009, 0x000A, 0x000B, 0x000C, 0x000D, 0x0020, 0x0085, 0x00A0, 0x1680,
		0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200A,
		0x2028, 0x2029, 0x202F, 0x205F, 0x3000}
	for _, point := range whitespace {
		if err := text.VisitJSON(string(point)); err == nil {
			t.Errorf("whitespace-only U+%04X accepted", point)
		}
	}
	if err := text.VisitJSON(string(whitespace)); err == nil {
		t.Error("whitespace-only text accepted")
	}
}

func TestSeverityIsOpenWithKinOpenAPI(t *testing.T) {
	level := schema(t, loadContract(t), "ErrorSeverity")
	for _, value := range []string{"info", "warning", "error", "future_level"} {
		if err := level.VisitJSON(value); err != nil {
			t.Errorf("severity %q rejected: %v", value, err)
		}
	}
	for _, value := range []any{nil, 1, []any{}, map[string]any{}} {
		if err := level.VisitJSON(value); err == nil {
			t.Errorf("invalid severity of type %T accepted", value)
		}
	}
}

func TestPresentationIsOptionalNonNullWithKinOpenAPI(t *testing.T) {
	doc := loadContract(t)
	cases := []struct {
		name   string
		value  map[string]any
		fields []string
	}{
		{"Error", map[string]any{"code": "RATE_LIMITED", "message": "Rate limited", "retry_at": "2026-10-25T20:00:00+03:00"}, []string{"user_message", "severity", "retryable"}},
		{"SseError", map[string]any{"code": "NEW_ERROR", "message": "Domain failure"}, []string{"user_message", "severity"}},
		{"StreamError", map[string]any{"code": "CANCELLED", "message": "Import cancelled"}, []string{"user_message", "severity"}},
		{"AnswerErrorEvent", map[string]any{"code": "MODEL_ERROR", "message": "Answer failed", "text": "Прежний безопасный текст", "retryable": false, "remaining_limit": 5}, []string{"user_message", "severity"}},
		{"Message", map[string]any{"id": "answer-1", "role": "assistant", "created_at": "2026-10-25T18:00:00+03:00", "currency": "RUB", "status": "failed", "error_code": "OFF_TOPIC", "error_text": "Прежний безопасный текст", "retryable": false}, []string{"error_user_message", "error_severity"}},
	}
	for _, item := range cases {
		t.Run(item.name, func(t *testing.T) {
			wire := schema(t, doc, item.name)
			if err := wire.VisitJSON(item.value); err != nil {
				t.Fatalf("absent optional fields rejected: %v", err)
			}
			for _, field := range item.fields {
				item.value[field] = nil
				if err := wire.VisitJSON(item.value); err == nil {
					t.Errorf("present-null %s accepted", field)
				}
				delete(item.value, field)
			}
		})
	}
}

func TestRESTRetryableIsOptionalNonNullBooleanWithKinOpenAPI(t *testing.T) {
	wire := schema(t, loadContract(t), "Error")
	value := map[string]any{"code": "RATE_LIMITED", "message": "Rate limited", "user_message": "Попробуйте позже.",
		"severity": "warning", "retry_at": "2026-10-25T20:00:00+03:00"}
	if err := wire.VisitJSON(value); err != nil {
		t.Fatalf("absent retryable rejected: %v", err)
	}
	for _, allowed := range []bool{true, false} {
		value["retryable"] = allowed
		if err := wire.VisitJSON(value); err != nil {
			t.Errorf("retryable=%t rejected: %v", allowed, err)
		}
	}
	for _, invalid := range []any{nil, 0, float64(1), "true", "false", []any{}, map[string]any{}} {
		value["retryable"] = invalid
		err := wire.VisitJSON(value)
		var fieldError *openapi3.SchemaError
		if !errors.As(err, &fieldError) || strings.Join(fieldError.JSONPointer(), "/") != "retryable" {
			t.Errorf("invalid retryable type %T must fail locally at retryable: %v", invalid, err)
		}
		delete(value, "retryable")
		if err := wire.VisitJSON(value); err != nil {
			t.Errorf("ignoring malformed optional retryable did not leave a valid wire payload: %v", err)
		}
	}
}
