#!/usr/bin/env bash
# Regenerates gen/go from proto/ (FIN-116). Plugin and buf versions come from tools/go.mod, so every run
# (locally and in CI) produces the same code. Usage: scripts/generate.sh
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
bin="$root/.bin"
mkdir -p "$bin"

# Build the pinned tools into .bin (ignored by git).
(cd "$root/tools" && go build -o "$bin/" \
  github.com/bufbuild/buf/cmd/buf \
  google.golang.org/protobuf/cmd/protoc-gen-go \
  google.golang.org/grpc/cmd/protoc-gen-go-grpc \
  ./protoc-gen-finerr)

# Generated files are replaced as a whole: a removed .proto must not leave stale code behind. Hand-written files
# (go.mod, tests) stay.
find "$root/gen/go" -name '*.pb.go' -delete

(cd "$root/proto" && PATH="$bin:$PATH" buf generate)

# gen/go/go.mod is kept by hand (runtime versions are part of the published module): the script does not touch it.
# A new import in the generated code (another well-known type) needs `go get` there; CI's go build catches it.
