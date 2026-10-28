package openapicheck_test

import (
	"strings"
	"testing"
)

func TestPhoneVerificationRequiresSeparateConfirmations(t *testing.T) {
	doc := loadContract(t)
	request := schema(t, doc, "StartPhoneVerificationRequest")
	valid := map[string]any{"phone": "+79001234567", "pd_consent_accepted": true, "pd_consent_version": "2026-09-28", "age_confirmed": true}
	if err := request.VisitJSON(valid); err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"phone", "pd_consent_accepted", "pd_consent_version", "age_confirmed"} {
		t.Run(field, func(t *testing.T) {
			for _, change := range []string{"absent", "null", "wrong_type"} {
				value := make(map[string]any, len(valid))
				for k, v := range valid {
					value[k] = v
				}
				switch change {
				case "absent":
					delete(value, field)
				case "null":
					value[field] = nil
				case "wrong_type":
					value[field] = 42
				}
				if err := request.VisitJSON(value); err == nil {
					t.Errorf("%s accepted", change)
				}
			}
		})
	}
	// False is structurally valid: the service owns the consent/age domain refusal and error order.
	for _, field := range []string{"pd_consent_accepted", "age_confirmed"} {
		valid[field] = false
		if err := request.VisitJSON(valid); err != nil {
			t.Fatalf("false %s must reach the service: %v", field, err)
		}
		valid[field] = true
	}
	for _, version := range []string{"x", strings.Repeat("я", 64)} {
		valid["pd_consent_version"] = version
		if err := request.VisitJSON(valid); err != nil {
			t.Errorf("valid version rejected: %v", err)
		}
	}
	for _, version := range []string{"", strings.Repeat("я", 65)} {
		valid["pd_consent_version"] = version
		if err := request.VisitJSON(valid); err == nil {
			t.Error("out-of-bounds version accepted")
		}
	}
	op := doc.Paths.Value("/v1/auth/phone").Post
	for _, status := range []int{400, 403, 409, 422, 429} {
		if op.Responses.Status(status) == nil {
			t.Errorf("missing phone refusal %d", status)
		}
	}
}

func TestRegisterPreservesOriginalConsentAssertions(t *testing.T) {
	request := schema(t, loadContract(t), "RegisterRequest")
	value := map[string]any{"registration_token": "reg_synthetic", "pd_consent_accepted": true, "pd_consent_version": "2026-09-28", "timezone": "Europe/Moscow"}
	if err := request.VisitJSON(value); err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"pd_consent_accepted", "pd_consent_version"} {
		original := value[field]
		delete(value, field)
		if err := request.VisitJSON(value); err == nil {
			t.Errorf("missing original assertion %s accepted", field)
		}
		value[field] = original
	}
}
