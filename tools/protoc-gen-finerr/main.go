// Command protoc-gen-finerr generates package finerr from the error_spec options of the error registry
// (proto/finassist/common/v1/errors.proto, docs/grpc-api.md 5.2): one constructor per ErrorReason value for the
// services and the REST translation table for bank-bff. Nobody writes the ErrorInfo or the mapping by hand.
//
// The plugin reads error_spec dynamically from the descriptors of the request, so it does not depend on the Go code
// generated from error_options.proto (changing that file needs no bootstrap step).
//
// Parameter: go_import_path=<import path of package finerr>; the file is written to
// <path relative to the module>/finerr.pb.go, as protoc-gen-go does with paths=import + module=.
package main

import (
	"bytes"
	"errors"
	"fmt"
	"go/format"
	"io"
	"os"
	"strings"
	"unicode"

	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/reflect/protodesc"
	"google.golang.org/protobuf/reflect/protoreflect"
	"google.golang.org/protobuf/reflect/protoregistry"
	"google.golang.org/protobuf/types/descriptorpb"
	"google.golang.org/protobuf/types/dynamicpb"
	"google.golang.org/protobuf/types/pluginpb"
)

const (
	registryEnum  = "finassist.common.v1.ErrorReason"
	specExtension = "finassist.common.v1.error_spec"
	// Error detail values of finassist.common.v1.ErrorDetail.
	detailBadRequest = "ERROR_DETAIL_BAD_REQUEST"
	detailRetryInfo  = "ERROR_DETAIL_RETRY_INFO"
	// Prefix of finassist.common.v1.GrpcCode values; the rest of the name is the grpc-go codes.Code name in
	// UPPER_SNAKE_CASE, and the numbers match (error_options.proto).
	grpcCodePrefix = "GRPC_CODE_"
)

func main() {
	if err := run(os.Stdin, os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, "protoc-gen-finerr:", err)
		os.Exit(1)
	}
}

func run(in io.Reader, out io.Writer) error {
	data, err := io.ReadAll(in)
	if err != nil {
		return err
	}
	req := &pluginpb.CodeGeneratorRequest{}
	if err := proto.Unmarshal(data, req); err != nil {
		return err
	}
	resp := generate(req)
	resp.SupportedFeatures = proto.Uint64(uint64(pluginpb.CodeGeneratorResponse_FEATURE_PROTO3_OPTIONAL))
	b, err := proto.Marshal(resp)
	if err != nil {
		return err
	}
	_, err = out.Write(b)
	return err
}

func generate(req *pluginpb.CodeGeneratorRequest) *pluginpb.CodeGeneratorResponse {
	fail := func(err error) *pluginpb.CodeGeneratorResponse {
		return &pluginpb.CodeGeneratorResponse{Error: proto.String(err.Error())}
	}
	importPath, err := parseParameter(req.GetParameter())
	if err != nil {
		return fail(err)
	}
	files, err := protodesc.NewFiles(&descriptorpb.FileDescriptorSet{File: req.GetProtoFile()})
	if err != nil {
		return fail(err)
	}
	// Generate only when the registry is among the files to generate: one output for the whole module.
	var registryFile protoreflect.FileDescriptor
	for _, name := range req.GetFileToGenerate() {
		fd, err := files.FindFileByPath(name)
		if err != nil {
			return fail(err)
		}
		if fd.Enums().ByName(protoreflect.FullName(registryEnum).Name()) != nil && fd.Package() == protoreflect.FullName(registryEnum).Parent() {
			registryFile = fd
		}
	}
	if registryFile == nil {
		return &pluginpb.CodeGeneratorResponse{}
	}
	codes, err := readRegistry(files)
	if err != nil {
		return fail(err)
	}
	src, err := render(importPath, registryFile, codes)
	if err != nil {
		return fail(err)
	}
	return &pluginpb.CodeGeneratorResponse{File: []*pluginpb.CodeGeneratorResponse_File{{
		Name:    proto.String(outputPath(importPath)),
		Content: proto.String(src),
	}}}
}

func parseParameter(param string) (string, error) {
	var importPath string
	for _, kv := range strings.Split(param, ",") {
		if kv == "" {
			continue
		}
		k, v, _ := strings.Cut(kv, "=")
		switch k {
		case "go_import_path":
			importPath = v
		default:
			return "", fmt.Errorf("unknown parameter %q", k)
		}
	}
	if importPath == "" {
		return "", errors.New("parameter go_import_path is required")
	}
	return importPath, nil
}

// outputPath strips the module prefix the same way buf strips it for protoc-gen-go (opt: module=...): the last
// "/gen/go/" segment ends the module path.
func outputPath(importPath string) string {
	if i := strings.LastIndex(importPath, "/gen/go/"); i >= 0 {
		return importPath[i+len("/gen/go/"):] + "/finerr.pb.go"
	}
	return importPath + "/finerr.pb.go"
}

type code struct {
	Name         string   // ErrorReason value name, the ErrorInfo.reason and the REST code.
	Comment      string   // Leading comment of the value.
	GRPCCode     string   // grpc-go codes.Code constant name.
	HTTPStatus   int32    // REST status bank-bff answers with.
	MetadataKeys []string // Required ErrorInfo.metadata keys, in declaration order.
	BadRequest   bool     // google.rpc.BadRequest is attached.
	RetryInfo    bool     // google.rpc.RetryInfo is attached.
}

func readRegistry(files *protoregistry.Files) ([]code, error) {
	d, err := files.FindDescriptorByName(registryEnum)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", registryEnum, err)
	}
	enum, ok := d.(protoreflect.EnumDescriptor)
	if !ok {
		return nil, fmt.Errorf("%s is not an enum", registryEnum)
	}
	xd, err := files.FindDescriptorByName(specExtension)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", specExtension, err)
	}
	ext := dynamicpb.NewExtensionType(xd.(protoreflect.ExtensionDescriptor))
	types := new(protoregistry.Types)
	if err := types.RegisterExtension(ext); err != nil {
		return nil, err
	}

	var out []code
	values := enum.Values()
	for i := 0; i < values.Len(); i++ {
		v := values.Get(i)
		if v.Number() == 0 {
			continue
		}
		spec, err := specOf(v, ext, types)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", v.Name(), err)
		}
		c := code{Name: string(v.Name()), Comment: comment(v)}
		fields := spec.Descriptor().Fields()
		grpcCode := spec.Get(fields.ByName("grpc_code")).Enum()
		gv := fields.ByName("grpc_code").Enum().Values().ByNumber(grpcCode)
		if grpcCode == 0 || gv == nil {
			return nil, fmt.Errorf("%s: error_spec.grpc_code is not set", v.Name())
		}
		c.GRPCCode = goCodeName(strings.TrimPrefix(string(gv.Name()), grpcCodePrefix))
		c.HTTPStatus = int32(spec.Get(fields.ByName("http_status")).Int())
		if c.HTTPStatus < 400 || c.HTTPStatus > 599 {
			return nil, fmt.Errorf("%s: error_spec.http_status %d is not an error status", v.Name(), c.HTTPStatus)
		}
		keys := spec.Get(fields.ByName("metadata_keys")).List()
		for j := 0; j < keys.Len(); j++ {
			c.MetadataKeys = append(c.MetadataKeys, keys.Get(j).String())
		}
		details := spec.Get(fields.ByName("details")).List()
		detailEnum := fields.ByName("details").Enum()
		for j := 0; j < details.Len(); j++ {
			dv := detailEnum.Values().ByNumber(details.Get(j).Enum())
			if dv == nil {
				return nil, fmt.Errorf("%s: unknown error_spec.details value %d", v.Name(), details.Get(j).Enum())
			}
			switch dv.Name() {
			case detailBadRequest:
				c.BadRequest = true
			case detailRetryInfo:
				c.RetryInfo = true
			default:
				return nil, fmt.Errorf("%s: error_spec.details %s is not supported by the generator", v.Name(), dv.Name())
			}
		}
		out = append(out, c)
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("%s has no codes", registryEnum)
	}
	return out, nil
}

// specOf re-parses the options of an enum value with the extension resolver: protodesc keeps an unknown extension
// as unknown bytes. The options are parsed into a dynamic message of the request's own EnumValueOptions descriptor:
// the generated descriptorpb type does not accept an extension declared against another descriptor instance.
func specOf(v protoreflect.EnumValueDescriptor, ext protoreflect.ExtensionType, types *protoregistry.Types) (protoreflect.Message, error) {
	opts, ok := v.Options().(*descriptorpb.EnumValueOptions)
	if !ok || opts == nil {
		return nil, errors.New("error_spec is missing")
	}
	raw, err := proto.Marshal(opts)
	if err != nil {
		return nil, err
	}
	xd := ext.TypeDescriptor()
	parsed := dynamicpb.NewMessage(xd.ContainingMessage())
	if err := (proto.UnmarshalOptions{Resolver: types}).Unmarshal(raw, parsed); err != nil {
		return nil, err
	}
	if !parsed.Has(xd) {
		return nil, errors.New("error_spec is missing")
	}
	return parsed.Get(xd).Message(), nil
}

func comment(v protoreflect.EnumValueDescriptor) string {
	loc := v.ParentFile().SourceLocations().ByDescriptor(v)
	return strings.Join(strings.Fields(loc.LeadingComments), " ")
}

// goCodeName turns INVALID_ARGUMENT into InvalidArgument (grpc-go codes constants).
func goCodeName(s string) string {
	return camel(strings.ToLower(s), true)
}

// camel turns snake_case into CamelCase (upper) or camelCase.
func camel(s string, upper bool) string {
	var b strings.Builder
	next := upper
	for _, r := range s {
		if r == '_' {
			next = true
			continue
		}
		if next {
			b.WriteRune(unicode.ToUpper(r))
			next = false
		} else {
			b.WriteRune(unicode.ToLower(r))
		}
	}
	return b.String()
}

// reserved are Go keywords and names of the generated code a metadata parameter must not shadow.
var reserved = map[string]bool{
	"break": true, "case": true, "chan": true, "const": true, "continue": true, "default": true, "defer": true,
	"else": true, "fallthrough": true, "for": true, "func": true, "go": true, "goto": true, "if": true,
	"import": true, "interface": true, "map": true, "package": true, "range": true, "return": true,
	"select": true, "struct": true, "switch": true, "type": true, "var": true,
	"err": true, "md": true, "st": true, "violations": true, "retryDelay": true, "codes": true, "status": true,
}

// retryAtKey is the metadata key that duplicates RetryInfo (RATE_LIMITED: error.retry_at and Retry-After).
const retryAtKey = "retry_at"

func hasKey(keys []string, key string) bool {
	for _, k := range keys {
		if k == key {
			return true
		}
	}
	return false
}

// isTimeKey: a metadata key with the _at suffix carries a moment in RFC 3339 (error_options.proto); the constructor
// takes time.Time (its Location sets the offset in the string) and ToREST rejects a value that does not parse or is
// the zero time.
func isTimeKey(key string) bool {
	return strings.HasSuffix(key, "_at")
}

func paramName(key string) string {
	p := camel(key, false)
	if reserved[p] {
		p += "Value"
	}
	return p
}

func render(importPath string, registry protoreflect.FileDescriptor, codes []code) (string, error) {
	commonPkg, ok := registry.Options().(*descriptorpb.FileOptions)
	if !ok || commonPkg.GetGoPackage() == "" {
		return "", fmt.Errorf("%s has no go_package", registry.Path())
	}
	commonImport, _, _ := strings.Cut(commonPkg.GetGoPackage(), ";")
	pkg := importPath[strings.LastIndex(importPath, "/")+1:]

	var b bytes.Buffer
	p := func(format string, args ...any) { fmt.Fprintf(&b, format+"\n", args...) }
	p("// Code generated by protoc-gen-finerr. DO NOT EDIT.")
	p("// source: %s", registry.Path())
	p("")
	p("// Package %s builds the registry errors of finassist services and translates them for bank-bff", pkg)
	p("// (docs/grpc-api.md, section 5). Constructors and the table come from the error_spec options of ErrorReason;")
	p("// services return errors only through this package.")
	p("package %s", pkg)
	p("")
	p("import (")
	p("\t\"regexp\"")
	p("\t\"strings\"")
	p("\t\"time\"")
	p("")
	p("\tcommonv1 %q", commonImport)
	p("\t\"google.golang.org/genproto/googleapis/rpc/errdetails\"")
	p("\t\"google.golang.org/grpc/codes\"")
	p("\t\"google.golang.org/grpc/status\"")
	p("\t\"google.golang.org/protobuf/protoadapt\"")
	p("\t\"google.golang.org/protobuf/types/known/durationpb\"")
	p(")")
	p("")
	p("// Domain is the google.rpc.ErrorInfo domain of every registry error.")
	p("const Domain = \"finassist\"")
	p("")
	p("// Spec describes how a registry code travels between services and how bank-bff answers it in REST.")
	p("type Spec struct {")
	p("\t// Reason is the ErrorReason value; its name is ErrorInfo.reason and the REST error code.")
	p("\tReason commonv1.ErrorReason")
	p("\t// GRPCCode is the status code the service returns.")
	p("\tGRPCCode codes.Code")
	p("\t// HTTPStatus is the status bank-bff answers the client with.")
	p("\tHTTPStatus int")
	p("\t// MetadataKeys are the ErrorInfo.metadata keys the service must fill; no other keys are allowed.")
	p("\tMetadataKeys []string")
	p("\t// BadRequest: google.rpc.BadRequest is attached (bank-bff maps it to REST details[]).")
	p("\tBadRequest bool")
	p("\t// RetryInfo: google.rpc.RetryInfo is attached (bank-bff maps it to Retry-After).")
	p("\tRetryInfo bool")
	p("}")
	p("")
	p("// specs is the registry by ErrorInfo.reason — the REST translation table of bank-bff.")
	p("var specs = map[string]Spec{")
	for _, c := range codes {
		keys := "nil"
		if len(c.MetadataKeys) > 0 {
			keys = fmt.Sprintf("%#v", c.MetadataKeys)
		}
		p("\t%q: {Reason: commonv1.ErrorReason_%s, GRPCCode: codes.%s, HTTPStatus: %d, MetadataKeys: %s, BadRequest: %t, RetryInfo: %t},",
			c.Name, c.Name, c.GRPCCode, c.HTTPStatus, keys, c.BadRequest, c.RetryInfo)
	}
	p("}")
	p("")
	for _, c := range codes {
		fn := camel(strings.ToLower(c.Name), true)
		var params, md []string
		for _, k := range c.MetadataKeys {
			if isTimeKey(k) {
				params = append(params, paramName(k)+" time.Time")
				md = append(md, fmt.Sprintf("%q: %s.Format(time.RFC3339)", k, paramName(k)))
			} else {
				params = append(params, paramName(k)+" string")
				md = append(md, fmt.Sprintf("%q: %s", k, paramName(k)))
			}
		}
		// RetryInfo and retry_at say the same thing; when both are required, the delay is derived from retry_at so the
		// REST body and Retry-After cannot disagree.
		retryFromAt := c.RetryInfo && hasKey(c.MetadataKeys, retryAtKey)
		if c.RetryInfo && !retryFromAt {
			params = append(params, "retryDelay time.Duration")
		}
		if c.BadRequest {
			params = append(params, "violations ...*errdetails.BadRequest_FieldViolation")
		}
		if c.Comment != "" {
			p("// %s returns %s: %s", fn, c.Name, c.Comment)
		} else {
			p("// %s returns %s.", fn, c.Name)
		}
		p("func %s(%s) error {", fn, strings.Join(params, ", "))
		mdArg := "nil"
		if len(md) > 0 {
			mdArg = "map[string]string{" + strings.Join(md, ", ") + "}"
		}
		var extra []string
		if retryFromAt {
			extra = append(extra, fmt.Sprintf("&errdetails.RetryInfo{RetryDelay: durationpb.New(delayUntil(%s))}", paramName(retryAtKey)))
		} else if c.RetryInfo {
			extra = append(extra, "&errdetails.RetryInfo{RetryDelay: durationpb.New(retryDelay)}")
		}
		if c.BadRequest {
			extra = append(extra, "&errdetails.BadRequest{FieldViolations: violations}")
		}
		args := ""
		if len(extra) > 0 {
			args = ", " + strings.Join(extra, ", ")
		}
		p("\treturn build(%q, codes.%s, %s%s)", c.Name, c.GRPCCode, mdArg, args)
		p("}")
		p("")
	}
	b.WriteString(runtime)
	src, err := format.Source(b.Bytes())
	if err != nil {
		return "", fmt.Errorf("format generated code: %w\n%s", err, b.String())
	}
	return string(src), nil
}

// runtime is the fixed part of the package: building a status and reading it back in bank-bff.
const runtime = `
func build(reason string, code codes.Code, md map[string]string, details ...protoadapt.MessageV1) error {
	st := status.New(code, reason)
	all := make([]protoadapt.MessageV1, 0, len(details)+1)
	all = append(all, &errdetails.ErrorInfo{Domain: Domain, Reason: reason, Metadata: md})
	all = append(all, details...)
	withDetails, err := st.WithDetails(all...)
	if err != nil {
		// Only a nil detail fails here; the status without details is still a correct gRPC error, and bank-bff
		// answers it with 500 and the unmapped-error metric.
		return st.Err()
	}
	return withDetails.Err()
}

// Lookup returns the spec of a registry code by ErrorInfo.reason; ok is false for a reason outside the registry.
func Lookup(reason string) (spec Spec, ok bool) {
	spec, ok = specs[reason]
	if ok {
		spec.MetadataKeys = append([]string(nil), spec.MetadataKeys...)
	}
	return spec, ok
}

// rfc3339 is stricter than time.Parse: the offset is within ±14:59 and minutes within 00–59.
var rfc3339 = regexp.MustCompile("^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(\\.\\d+)?(Z|[+-](0\\d|1[0-4]):[0-5]\\d)$")

// isMoment reports whether v is a non-zero RFC 3339 moment with a valid offset.
func isMoment(v string) bool {
	if !rfc3339.MatchString(v) {
		return false
	}
	t, err := time.Parse(time.RFC3339, v)
	return err == nil && !t.IsZero()
}

// delayUntil is the Retry-After for a moment: whole seconds, rounded up, at least one.
func delayUntil(at time.Time) time.Duration {
	d := time.Until(at)
	if d < time.Second {
		return time.Second
	}
	if rest := d % time.Second; rest != 0 {
		d += time.Second - rest
	}
	return d
}

// REST is a registry error as bank-bff answers it.
type REST struct {
	// Spec of the code; Spec.Reason's name is the REST error code.
	Spec Spec
	// Code is the REST error code (ErrorInfo.reason).
	Code string
	// Metadata is ErrorInfo.metadata restricted to Spec.MetadataKeys.
	Metadata map[string]string
	// Violations from google.rpc.BadRequest, when Spec.BadRequest; at least one. REST details[]: field → field,
	// reason → code (REQUIRED, INVALID_FORMAT, …), description → message (for logs, not shown in UI);
	// localized_message is not used.
	Violations []*errdetails.BadRequest_FieldViolation
	// RetryAfter from google.rpc.RetryInfo, when Spec.RetryInfo; always positive then (REST Retry-After).
	RetryAfter time.Duration
}

// ToREST reads a registry error from a gRPC error. ok is false when the error carries no finassist ErrorInfo, the
// reason is not in the registry, a required metadata key is missing or empty (or, for an *_at key, not a non-zero
// RFC 3339 moment), or a required detail is missing (RetryInfo with a positive delay, BadRequest with at least one
// violation, each with a reason): bank-bff then answers 500 INTERNAL_ERROR and counts finassist_unmapped_error_total. The status message never goes to the client.
func ToREST(err error) (rest REST, ok bool) {
	st, isStatus := status.FromError(err)
	if !isStatus || st.Code() == codes.OK {
		return REST{}, false
	}
	var info *errdetails.ErrorInfo
	for _, d := range st.Details() {
		switch d := d.(type) {
		case *errdetails.ErrorInfo:
			if d.GetDomain() == Domain && info == nil {
				info = d
			}
		case *errdetails.BadRequest:
			rest.Violations = append(rest.Violations, d.GetFieldViolations()...)
		case *errdetails.RetryInfo:
			if d.GetRetryDelay() != nil {
				rest.RetryAfter = d.GetRetryDelay().AsDuration()
			}
		}
	}
	if info == nil {
		return REST{}, false
	}
	spec, known := Lookup(info.GetReason())
	if !known {
		return REST{}, false
	}
	rest.Spec, rest.Code = spec, info.GetReason()
	if len(spec.MetadataKeys) > 0 {
		rest.Metadata = make(map[string]string, len(spec.MetadataKeys))
		for _, k := range spec.MetadataKeys {
			v, present := info.GetMetadata()[k]
			if !present || v == "" {
				return REST{}, false
			}
			if strings.HasSuffix(k, "_at") && !isMoment(v) {
				return REST{}, false
			}
			rest.Metadata[k] = v
		}
	}
	if !spec.BadRequest {
		rest.Violations = nil
	} else if len(rest.Violations) == 0 {
		return REST{}, false
	}
	for _, v := range rest.Violations {
		if v.GetReason() == "" {
			return REST{}, false
		}
	}
	if !spec.RetryInfo {
		rest.RetryAfter = 0
	} else if rest.RetryAfter <= 0 {
		return REST{}, false
	}
	return rest, true
}
`
