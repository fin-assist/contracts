package openapicheck_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/getkin/kin-openapi/openapi3filter"
	"github.com/getkin/kin-openapi/routers/legacy"
)

func TestJSONRequestBudgetAndResponseScope(t *testing.T) {
	doc := loadContract(t)
	expected := map[string]bool{"startPhoneVerification": true, "register": true, "refreshTokens": true, "logout": true, "grantAssistantConsent": true, "askAssistant": true}
	seen := map[string]bool{}
	for path, item := range doc.Paths.Map() {
		for method, op := range item.Operations() {
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
			value := doc.Components.Responses["RequestTooLarge"].Value.Content["application/json"].Example
			body, err := json.Marshal(value)
			if err != nil {
				t.Fatal(err)
			}
			var result struct {
				Error struct {
					Code      string `json:"code"`
					Retryable *bool  `json:"retryable"`
				} `json:"error"`
			}
			if err := json.Unmarshal(body, &result); err != nil {
				t.Fatal(err)
			}
			if result.Error.Code != "REQUEST_TOO_LARGE" || result.Error.Retryable == nil || *result.Error.Retryable {
				t.Fatal("transport refusal must use its BFF-only code and deny unchanged retry")
			}
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
