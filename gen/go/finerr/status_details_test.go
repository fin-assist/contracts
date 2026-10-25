package finerr

import (
	"errors"
	"fmt"
	"testing"
	"time"

	assistantv1 "github.com/fin-assist/contracts/gen/go/finassist/assistant/v1"
	commonv1 "github.com/fin-assist/contracts/gen/go/finassist/common/v1"
	statementdatav1 "github.com/fin-assist/contracts/gen/go/finassist/statementdata/v1"
	"google.golang.org/genproto/googleapis/rpc/errdetails"
	statuspb "google.golang.org/genproto/googleapis/rpc/status"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/anypb"
	"google.golang.org/protobuf/types/known/durationpb"
	"google.golang.org/protobuf/types/known/wrapperspb"
)

func packDetail(t *testing.T, value proto.Message) *anypb.Any {
	t.Helper()
	detail, err := anypb.New(value)
	if err != nil {
		t.Fatal(err)
	}
	return detail
}

func wireStatus(t *testing.T, value *statuspb.Status) *statuspb.Status {
	t.Helper()
	data, err := proto.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	result := new(statuspb.Status)
	if err := proto.Unmarshal(data, result); err != nil {
		t.Fatal(err)
	}
	return result
}

func TestRequestRetryInfoWireAndReplacement(t *testing.T) {
	base := RateLimited(time.Now().Add(time.Minute))
	before := status.Convert(base).Proto()
	unknown := packDetail(t, wrapperspb.String("opaque future extension"))
	withUnknown := status.Convert(base).Proto()
	withUnknown.Details = append(withUnknown.Details, unknown)
	for _, permission := range []bool{false, true} {
		err := WithRequestRetryInfo(status.FromProto(withUnknown).Err(), permission)
		rest, ok := ToREST(status.FromProto(wireStatus(t, status.Convert(err).Proto())).Err())
		if !ok || rest.RequestRetryable == nil || *rest.RequestRetryable != permission || rest.Code != "RATE_LIMITED" || rest.RetryAfter <= 0 {
			t.Fatalf("permission %t: %+v, %t", permission, rest, ok)
		}
		if rest.Metadata["retry_at"] == "" {
			t.Fatal("retry timestamp lost")
		}
	}
	updated := WithRequestRetryInfo(WithRequestRetryInfo(base, true), false)
	adviceCount := 0
	for _, detail := range status.Convert(updated).Proto().Details {
		if detail.MessageIs(&commonv1.RequestRetryInfo{}) {
			adviceCount++
		}
	}
	rest, ok := ToREST(updated)
	if !ok || rest.RequestRetryable == nil || *rest.RequestRetryable || adviceCount != 1 {
		t.Fatalf("replacement did not yield one false advice: %+v, count %d", rest, adviceCount)
	}
	if !proto.Equal(before, status.Convert(base).Proto()) {
		t.Fatal("helper mutated the original status")
	}
	wrapped := fmt.Errorf("PRIVATE_CONNECTION_STRING: %w", base)
	clean := status.Convert(WithRequestRetryInfo(wrapped, true)).Proto()
	if clean.Message != before.Message {
		t.Fatalf("wrapper diagnostics were serialized: %q", clean.Message)
	}
	legacy, ok := ToREST(base)
	if !ok || legacy.RequestRetryable != nil {
		t.Fatal("legacy absence became a permission")
	}
	plain := errors.New("plain failure")
	if WithRequestRetryInfo(nil, true) != nil || WithRequestRetryInfo(plain, true) != plain {
		t.Fatal("non-status errors were replaced")
	}
	keptUnknown := WithRequestRetryInfo(status.FromProto(withUnknown).Err(), true)
	found := false
	for _, detail := range status.Convert(keptUnknown).Proto().Details {
		found = found || proto.Equal(detail, unknown)
	}
	if !found {
		t.Fatal("unknown extension was dropped")
	}
}

func TestMalformedAndDuplicateRecognizedDetailsFailClosed(t *testing.T) {
	info := packDetail(t, &errdetails.ErrorInfo{Domain: Domain, Reason: "NOT_FOUND"})
	adviceTrue := packDetail(t, &commonv1.RequestRetryInfo{Retryable: true})
	adviceFalse := packDetail(t, &commonv1.RequestRetryInfo{})
	bad := packDetail(t, &errdetails.BadRequest{FieldViolations: []*errdetails.BadRequest_FieldViolation{{Reason: "REQUIRED"}}})
	retry := packDetail(t, &errdetails.RetryInfo{RetryDelay: durationpb.New(time.Second)})
	for _, detail := range []*anypb.Any{info, adviceTrue, bad, retry} {
		malformed := &anypb.Any{TypeUrl: detail.TypeUrl, Value: []byte{0xFF}}
		for _, extras := range [][]*anypb.Any{{detail, detail}, {malformed, adviceTrue}, {adviceTrue, malformed}} {
			wire := status.Convert(NotFound()).Proto()
			wire.Details = append(wire.Details, extras...)
			if rest, ok := ToREST(status.FromProto(wireStatus(t, wire)).Err()); ok {
				t.Fatalf("invalid recognized details granted a mapping: %+v", rest)
			}
		}
	}
	for _, pair := range [][]*anypb.Any{{adviceFalse, adviceTrue}, {adviceTrue, adviceFalse}} {
		wire := status.Convert(NotFound()).Proto()
		wire.Details = append(wire.Details, pair...)
		if _, ok := ToREST(status.FromProto(wire).Err()); ok {
			t.Fatal("contradictory request permissions were accepted")
		}
		if _, ok := ToREST(WithRequestRetryInfo(status.FromProto(wire).Err(), true)); ok {
			t.Fatal("helper laundered invalid duplicate details into a positive permission")
		}
	}
	for _, delay := range []*durationpb.Duration{nil, {Seconds: -1}, {Nanos: 1_000_000_000}, {Seconds: 315_576_000_000}} {
		wire := status.Convert(NotFound()).Proto()
		wire.Details = append(wire.Details, packDetail(t, &errdetails.RetryInfo{RetryDelay: delay}))
		if _, ok := ToREST(status.FromProto(wire).Err()); ok {
			t.Fatalf("invalid/unrepresentable retry delay accepted: %v", delay)
		}
	}
	// An unknown extension's opaque payload must not be decoded as a known message or fail the error.
	wire := status.Convert(WithRequestRetryInfo(NotFound(), true)).Proto()
	wire.Details = append(wire.Details, &anypb.Any{TypeUrl: "type.example/future.Extension", Value: []byte{0xFF}})
	rest, ok := ToREST(status.FromProto(wireStatus(t, wire)).Err())
	if !ok || rest.RequestRetryable == nil || !*rest.RequestRetryable {
		t.Fatal("unknown Any broke a valid known permission")
	}
}

func TestResultStatusIsOpenAndSeparateFromUnaryMapping(t *testing.T) {
	wire, err := NewResultStatus(codes.FailedPrecondition, "FUTURE_RESULT_FAILURE", map[string]string{"parameter": "value"})
	if err != nil {
		t.Fatal(err)
	}
	result, ok := DecodeResultStatus(wireStatus(t, wire))
	if !ok || result.Code != codes.FailedPrecondition || result.Reason != "FUTURE_RESULT_FAILURE" || result.Metadata["parameter"] != "value" {
		t.Fatalf("open result error not decoded: %+v %t", result, ok)
	}
	if _, ok := ToREST(status.FromProto(wire).Err()); ok {
		t.Fatal("terminal reason was implicitly added to the unary registry")
	}
	result.Metadata["parameter"] = "changed"
	again, _ := DecodeResultStatus(wire)
	if again.Metadata["parameter"] != "value" {
		t.Fatal("decoder exposes payload metadata")
	}
	for _, detail := range []*anypb.Any{
		packDetail(t, &commonv1.RequestRetryInfo{}),
		packDetail(t, &commonv1.RequestRetryInfo{Retryable: true}),
		packDetail(t, &errdetails.RetryInfo{RetryDelay: durationpb.New(time.Second)}),
	} {
		invalid := proto.Clone(wire).(*statuspb.Status)
		invalid.Details = append(invalid.Details, detail)
		if _, ok := DecodeResultStatus(invalid); ok {
			t.Fatal("request advice/wait accepted inside a stored result")
		}
	}
	for _, invalid := range []*statuspb.Status{nil, {}, {Code: int32(codes.Internal)}, {Code: 99, Details: wire.Details}} {
		if _, ok := DecodeResultStatus(invalid); ok {
			t.Fatalf("invalid nested error accepted: %v", invalid)
		}
	}
	if _, err := NewResultStatus(codes.OK, "FAILED", nil); err == nil {
		t.Fatal("OK accepted as failed-result status")
	}
	if _, err := NewResultStatus(codes.Internal, " ", nil); err == nil {
		t.Fatal("blank reason accepted")
	}
	if _, err := NewResultStatus(codes.Internal, "PRIVATE SQL", nil); err == nil {
		t.Fatal("diagnostics accepted as a machine reason")
	}
	if _, err := NewResultStatus(codes.Internal, "VALID_REASON", map[string]string{"bad key": "value"}); err == nil {
		t.Fatal("invalid metadata key accepted")
	}
	unknown := proto.Clone(wire).(*statuspb.Status)
	unknown.Details = append(unknown.Details, &anypb.Any{TypeUrl: "type.example/future.Extension", Value: []byte{0xFF}})
	if _, ok := DecodeResultStatus(unknown); !ok {
		t.Fatal("unknown result extension rejected")
	}
}

func TestNestedStatusPreservesLegacyWireFields(t *testing.T) {
	wire, err := NewResultStatus(codes.FailedPrecondition, "FUTURE_RESULT_FAILURE", nil)
	if err != nil {
		t.Fatal(err)
	}
	answer := &assistantv1.AnswerError{Code: assistantv1.AnswerErrorCode_ANSWER_ERROR_CODE_UNSPECIFIED, RemainingLimit: 5, Retryable: false, Status: wire}
	data, err := proto.Marshal(answer)
	if err != nil {
		t.Fatal(err)
	}
	restored := new(assistantv1.AnswerError)
	if err := proto.Unmarshal(data, restored); err != nil {
		t.Fatal(err)
	}
	if restored.GetRemainingLimit() != 5 || restored.GetRetryable() || !proto.Equal(restored.GetStatus(), wire) {
		t.Fatal("generic payload absorbed or altered domain data")
	}
	if number := answer.ProtoReflect().Descriptor().Fields().ByName("status").Number(); number != 6 {
		t.Fatalf("AnswerError.status tag %d", number)
	}
	if number := (&assistantv1.Message{}).ProtoReflect().Descriptor().Fields().ByName("error").Number(); number != 14 {
		t.Fatalf("Message.error tag %d", number)
	}
	if number := (&statementdatav1.ImportStreamError{}).ProtoReflect().Descriptor().Fields().ByName("status").Number(); number != 3 {
		t.Fatalf("ImportStreamError.status tag %d", number)
	}
	if number := (&assistantv1.WatchAnswerResponse{}).ProtoReflect().Descriptor().Fields().ByName("error").Number(); number != 7 {
		t.Fatalf("legacy terminal event tag changed: %d", number)
	}
}
