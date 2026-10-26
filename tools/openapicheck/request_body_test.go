package openapicheck_test

import (
	"bufio"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/getkin/kin-openapi/openapi3filter"
	"github.com/getkin/kin-openapi/routers/legacy"
)

func TestJSONRequestBudgetAndResponseScope(t *testing.T) {
	doc := loadContract(t)
	expected := map[string]bool{"startPhoneVerification": true, "register": true, "refreshTokens": true, "logout": true, "grantAssistantConsent": true, "askAssistant": true}
	bodyless := map[string]bool{"streamPhoneVerification": true, "getConsentDocument": true, "getProfile": true,
		"revokeAssistantConsent": true, "listMessages": true, "streamAnswer": true, "retryAnswer": true, "getAssistantLimit": true}
	seen := map[string]bool{}
	for path, item := range doc.Paths.Map() {
		for method, op := range item.Operations() {
			if bodyless[op.OperationID] {
				seen[op.OperationID] = true
				if op.RequestBody != nil {
					t.Errorf("%s must not declare requestBody", op.OperationID)
				}
			}
			_, budget := op.Extensions["x-min-request-body-bytes"]
			response := op.Responses.Status(http.StatusRequestEntityTooLarge)
			declared := response != nil && response.Ref == "#/components/responses/RequestTooLarge"
			if expected[op.OperationID] {
				seen[op.OperationID] = true
				if !budget || !declared || op.RequestBody == nil {
					t.Errorf("%s %s: missing JSON body budget or transport 413", method, path)
				}
				raw, err := json.Marshal(op.Extensions["x-min-request-body-bytes"])
				if err != nil || string(raw) != "65536" {
					t.Errorf("%s: minimum budget = %s, want 65536", op.OperationID, raw)
				}
			} else if budget || declared {
				t.Errorf("%s: FIN-339 JSON policy leaked to another operation", op.OperationID)
			}
		}
	}
	for name := range expected {
		if !seen[name] {
			t.Errorf("operation %s missing", name)
		}
	}
	for name := range bodyless {
		if !seen[name] {
			t.Errorf("bodyless operation %s missing", name)
		}
	}
	upload := doc.Paths.Value("/v1/statements").Post.Responses.Status(413)
	if upload == nil || upload.Ref != "#/components/responses/PayloadTooLarge" {
		t.Fatal("file upload 413 changed")
	}
}

func TestRequestTooLargeResponseWithKinOpenAPI(t *testing.T) {
	doc := loadContract(t)
	router, err := legacy.NewRouter(doc)
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct{ method, path string }{{"POST", "/v1/auth/phone"}, {"POST", "/v1/auth/register"}, {"POST", "/v1/auth/token"}, {"POST", "/v1/auth/logout"}, {"PUT", "/v1/profile/assistant-consent"}, {"POST", "/v1/assistant/messages"}} {
		t.Run(tc.path, func(t *testing.T) {
			request := httptest.NewRequest(tc.method, doc.Servers[0].URL+tc.path, nil)
			route, params, err := router.FindRoute(request)
			if err != nil {
				t.Fatal(err)
			}
			// Independent caller payload: validation must not rely only on the document's own example.
			body := []byte(`{"error":{"code":"REQUEST_TOO_LARGE","message":"JSON request body exceeds transport limit","retryable":false}}`)
			input := &openapi3filter.ResponseValidationInput{RequestValidationInput: &openapi3filter.RequestValidationInput{Request: request, PathParams: params, Route: route}, Status: 413, Header: http.Header{"Content-Type": []string{"application/json"}}}
			input.SetBodyBytes(body)
			if err := openapi3filter.ValidateResponse(context.Background(), input); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestMaximumQuestionEscapedRepresentationFitsMinimumBudget(t *testing.T) {
	// A legal 2000-code-point question may use JSON surrogate-pair escapes: 12 wire bytes per point.
	body := `{"text":"` + strings.Repeat(`\ud83e\ude99`, 2000) + `","transfer_mode":"with"}`
	if len(body) > 65536 || len(body) <= 16384 {
		t.Fatalf("unexpected escaped representation size %d", len(body))
	}
	var value any
	if err := json.Unmarshal([]byte(body), &value); err != nil {
		t.Fatal(err)
	}
	doc := loadContract(t)
	op := doc.Paths.Value("/v1/assistant/messages").Post
	if err := op.RequestBody.Value.Content["application/json"].Schema.Value.VisitJSON(value); err != nil {
		t.Fatal(err)
	}
}

func TestRequestBodyPolicyFixtures(t *testing.T) {
	doc := loadContract(t)
	router, err := legacy.NewRouter(doc)
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join("..", "..", "fixtures", "requests", "body-policy.json"))
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name             string  `json:"name"`
		Operation        string  `json:"operation_id"`
		Wire             string  `json:"wire"`
		BodyBytes        int     `json:"body_bytes"`
		TransportError   *string `json:"transport_error"`
		ValidateEncoding bool    `json:"validate_encoding_with_kin"`
	}
	if err := json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) == 0 {
		t.Fatal("no request body fixtures")
	}
	for _, tc := range cases {
		t.Run(tc.Name, func(t *testing.T) {
			request, err := http.ReadRequest(bufio.NewReader(strings.NewReader(tc.Wire)))
			if err != nil {
				t.Fatal(err)
			}
			body, err := io.ReadAll(io.LimitReader(request.Body, 65537))
			if err != nil {
				t.Fatal(err)
			}
			request.Body.Close()
			if len(body) != tc.BodyBytes {
				t.Fatalf("actual decoded HTTP framing bytes=%d, want %d", len(body), tc.BodyBytes)
			}
			request.URL.Scheme = "https"
			request.URL.Host = request.Host
			route, params, err := router.FindRoute(request)
			if err != nil {
				t.Fatal(err)
			}
			if route.Operation.OperationID != tc.Operation {
				t.Fatalf("fixture routed to %s", route.Operation.OperationID)
			}
			if tc.Operation == "retryAnswer" && route.Operation.RequestBody != nil {
				t.Fatal("bodyless retry declares requestBody")
			}
			// BFF enforces the nonempty-body policy; kin-openapi does not reject it merely because requestBody is absent.
			// BFF must also inspect every duplicate encoding value. That raw wire fixture is consumed by FIN-268.
			if tc.ValidateEncoding {
				request.Body = io.NopCloser(strings.NewReader(string(body)))
				err := openapi3filter.ValidateRequest(context.Background(), &openapi3filter.RequestValidationInput{Request: request, PathParams: params, Route: route})
				if (err != nil) != (tc.TransportError != nil) {
					t.Fatalf("encoding schema validation=%v, expected transport error=%v", err, tc.TransportError)
				}
			}
		})
	}
}

func TestRepairedAPIExampleFieldsWithKinOpenAPI(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("..", "..", "docs", "api.md"))
	if err != nil {
		t.Fatal(err)
	}
	response := func(section, status string) map[string]any {
		t.Helper()
		_, part, ok := strings.Cut(string(data), section)
		if !ok {
			t.Fatalf("missing section %s", section)
		}
		_, part, ok = strings.Cut(part, "Ответ `"+status+"`:")
		if !ok {
			t.Fatalf("missing response %s", status)
		}
		_, part, ok = strings.Cut(part, "```json\n")
		if !ok {
			t.Fatal("missing response JSON")
		}
		raw, _, ok := strings.Cut(part, "\n```")
		if !ok {
			t.Fatal("unterminated response JSON")
		}
		var value map[string]any
		if err := json.Unmarshal([]byte(raw), &value); err != nil {
			t.Fatal(err)
		}
		return value
	}
	doc := loadContract(t)
	if err := schema(t, doc, "AskResponse").VisitJSON(response("### 7.1 ", "201")); err != nil {
		t.Fatal(err)
	}
	history := response("### 7.3 ", "200")
	messages, ok := history["messages"].([]any)
	if !ok || len(messages) < 2 {
		t.Fatal("missing history example messages")
	}
	message, ok := messages[1].(map[string]any)
	if !ok {
		t.Fatal("invalid assistant example")
	}
	if err := schema(t, doc, "AnswerSource").VisitJSON(message["source"]); err != nil {
		t.Fatal(err)
	}
}
