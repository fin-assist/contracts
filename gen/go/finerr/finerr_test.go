package finerr

import (
	"errors"
	"testing"
	"time"

	commonv1 "github.com/fin-assist/contracts/gen/go/finassist/common/v1"
	"google.golang.org/genproto/googleapis/rpc/errdetails"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func TestRegistryIsComplete(t *testing.T) {
	for number, name := range commonv1.ErrorReason_name {
		if number == 0 {
			continue
		}
		spec, ok := Lookup(name)
		if !ok {
			t.Errorf("%s has no spec", name)
			continue
		}
		if spec.Reason.String() != name {
			t.Errorf("%s: spec reason %s", name, spec.Reason)
		}
	}
	if _, ok := Lookup("ERROR_REASON_UNSPECIFIED"); ok {
		t.Error("UNSPECIFIED must not be in the registry")
	}
}

func TestConstructorRoundTrip(t *testing.T) {
	err := NotFound()
	if got := status.Code(err); got != codes.NotFound {
		t.Fatalf("grpc code %v", got)
	}
	rest, ok := ToREST(err)
	if !ok || rest.Code != "NOT_FOUND" || rest.Spec.HTTPStatus != 404 || rest.Metadata != nil {
		t.Fatalf("ToREST = %+v, %v", rest, ok)
	}
}

func TestMetadataAndRetryInfo(t *testing.T) {
	rest, ok := ToREST(LimitExceeded("2026-10-17T00:00:00+03:00"))
	if !ok || rest.Spec.HTTPStatus != 403 || rest.Metadata["resets_at"] != "2026-10-17T00:00:00+03:00" {
		t.Fatalf("LimitExceeded: %+v, %v", rest, ok)
	}
	rest, ok = ToREST(RequestInProgress(3 * time.Second))
	if !ok || rest.Spec.HTTPStatus != 409 || rest.RetryAfter != 3*time.Second {
		t.Fatalf("RequestInProgress: %+v, %v", rest, ok)
	}
}

func TestViolations(t *testing.T) {
	err := ValidationError(&errdetails.BadRequest_FieldViolation{Field: "phone", Description: "format"})
	rest, ok := ToREST(err)
	if !ok || rest.Spec.HTTPStatus != 400 || len(rest.Violations) != 1 || rest.Violations[0].GetField() != "phone" {
		t.Fatalf("ValidationError: %+v, %v", rest, ok)
	}
}

func TestUnmapped(t *testing.T) {
	foreign, _ := status.New(codes.NotFound, "x").WithDetails(&errdetails.ErrorInfo{Domain: "other", Reason: "NOT_FOUND"})
	unknown, _ := status.New(codes.NotFound, "x").WithDetails(&errdetails.ErrorInfo{Domain: Domain, Reason: "NO_SUCH_CODE"})
	noMetadata, _ := status.New(codes.ResourceExhausted, "x").WithDetails(&errdetails.ErrorInfo{Domain: Domain, Reason: "LIMIT_EXCEEDED"})
	cases := map[string]error{
		"nil":            nil,
		"plain error":    errors.New("boom"),
		"bare status":    status.Error(codes.PermissionDenied, "caller not allowed"),
		"foreign domain": foreign.Err(),
		"unknown reason": unknown.Err(),
		"missing key":    noMetadata.Err(),
	}
	for name, err := range cases {
		if rest, ok := ToREST(err); ok {
			t.Errorf("%s: mapped to %+v", name, rest)
		}
	}
}

func TestLookupReturnsCopy(t *testing.T) {
	spec, _ := Lookup("LIMIT_EXCEEDED")
	spec.MetadataKeys[0] = "changed"
	again, _ := Lookup("LIMIT_EXCEEDED")
	if again.MetadataKeys[0] != "resets_at" {
		t.Fatal("Lookup exposes the registry")
	}
}
