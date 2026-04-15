#!/usr/bin/env bash
# setup_distill_toolchain.sh
#
# Distill 파이프라인이 필요로 하는 llama.cpp 툴체인 (convert_hf_to_gguf.py +
# llama-quantize + libllama) 을 **매칭되는 한 버전** 으로 소스 빌드하고 로컬에
# 설치한다. Homebrew bottle 은 buffer 가 있어 convert 스크립트와 quantize 바이너리
# 가 서로 다른 커밋에서 build 될 수 있고, 이 드리프트가 실제로 EXAONE / Kanana2
# 같은 신규 아키텍처를 깨뜨린다 (2026-04-16 EXAONE layer_norm_rms_epsilon 사례).
#
# 이 스크립트는 idempotent — 여러 번 실행해도 문제 없다. LLAMA_CPP_SRC 디렉터리가
# 이미 있으면 git pull + rebuild, 없으면 clone 후 build.
#
# Usage:
#   ./scripts/setup_distill_toolchain.sh           # default paths
#   LLAMA_CPP_SRC=/custom/path ./scripts/setup_distill_toolchain.sh
#
# 설치 후 환경변수 (쉘 rc 에 추가 권장):
#   export DISTILL_CONVERT_SCRIPT="$LLAMA_CPP_SRC/convert_hf_to_gguf.py"
#   export DISTILL_QUANTIZE_BIN="$LLAMA_CPP_SRC/build/bin/llama-quantize"
#
# 필수 사전 조건:
#   - git, cmake, python3 (uv venv 에 gguf/torch/transformers 있어야 convert 가능)
#   - macOS: Xcode command line tools (Metal backend 자동 감지)
#   - Linux: build-essential + (optional) CUDA toolkit

set -euo pipefail

# ── 경로 설정 ───────────────────────────────────────────────────────────────
LLAMA_CPP_SRC="${LLAMA_CPP_SRC:-$HOME/.cache/knowledge-local/llama.cpp}"
LLAMA_CPP_REPO="${LLAMA_CPP_REPO:-https://github.com/ggml-org/llama.cpp.git}"
LLAMA_CPP_REF="${LLAMA_CPP_REF:-master}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PATCHES_DIR="$REPO_ROOT/scripts/patches"

log() { echo "[setup-distill-toolchain] $*"; }
die() { echo "[setup-distill-toolchain] ERROR: $*" >&2; exit 1; }

# ── 1) llama.cpp clone / update ─────────────────────────────────────────────
if [ -d "$LLAMA_CPP_SRC/.git" ]; then
    log "Updating existing llama.cpp at $LLAMA_CPP_SRC"
    git -C "$LLAMA_CPP_SRC" fetch --depth 1 origin "$LLAMA_CPP_REF" \
        || die "git fetch failed (check network / SSL)"
    git -C "$LLAMA_CPP_SRC" reset --hard FETCH_HEAD
else
    log "Cloning llama.cpp → $LLAMA_CPP_SRC"
    mkdir -p "$(dirname "$LLAMA_CPP_SRC")"
    # SSL workaround for corporate MITM (env var GIT_SSL_NO_VERIFY 은 각자 쉘에서
    # 설정. 여기서는 강제 안 함).
    git clone --depth 1 --branch "$LLAMA_CPP_REF" "$LLAMA_CPP_REPO" "$LLAMA_CPP_SRC" \
        || die "git clone failed (check network / SSL / ref)"
fi

# ── 2) 로컬 패치 적용 (idempotent) ──────────────────────────────────────────
# 현재 적용 중인 패치:
#   - convert_hf_to_gguf_exaone.patch: ExaoneModel.set_gguf_parameters 에
#     add_layer_norm_rms_eps 호출 추가 (upstream 버그 fix)
if [ -d "$PATCHES_DIR" ]; then
    shopt -s nullglob
    for patch in "$PATCHES_DIR"/*.patch; do
        name="$(basename "$patch")"
        # 이미 적용됐는지 확인 — reverse apply 가 성공하면 already applied
        if git -C "$LLAMA_CPP_SRC" apply --reverse --check "$patch" 2>/dev/null; then
            log "Patch already applied: $name"
            continue
        fi
        if git -C "$LLAMA_CPP_SRC" apply --check "$patch" 2>/dev/null; then
            log "Applying patch: $name"
            git -C "$LLAMA_CPP_SRC" apply "$patch"
        else
            log "WARN: patch $name does not apply cleanly (upstream may have merged it)"
        fi
    done
    shopt -u nullglob
else
    log "No patches directory at $PATCHES_DIR (skipping)"
fi

# ── 3) cmake build (Release) ────────────────────────────────────────────────
log "Configuring cmake build (Release, shared libs)"
cmake -S "$LLAMA_CPP_SRC" -B "$LLAMA_CPP_SRC/build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=ON \
    -DLLAMA_METAL=ON \
    >/dev/null

JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
log "Building llama-quantize + llama-cli + libllama (-j $JOBS)"
cmake --build "$LLAMA_CPP_SRC/build" \
    --target llama-quantize llama-cli llama \
    -j "$JOBS"

# ── 4) 빌드 결과물 검증 ─────────────────────────────────────────────────────
QUANTIZE_BIN="$LLAMA_CPP_SRC/build/bin/llama-quantize"
CONVERT_SCRIPT="$LLAMA_CPP_SRC/convert_hf_to_gguf.py"

[ -x "$QUANTIZE_BIN" ]    || die "build did not produce $QUANTIZE_BIN"
[ -f "$CONVERT_SCRIPT" ]  || die "convert script missing at $CONVERT_SCRIPT"

log "✓ Built binaries at:"
log "    CONVERT_SCRIPT = $CONVERT_SCRIPT"
log "    QUANTIZE_BIN   = $QUANTIZE_BIN"

# ── 5) Python gguf 패키지 (uv venv) 를 소스와 매칭 ──────────────────────────
if [ -d "$REPO_ROOT/.venv" ]; then
    log "Installing gguf-py from source into uv venv"
    (cd "$REPO_ROOT" && uv pip install -e "$LLAMA_CPP_SRC/gguf-py" >/dev/null)
fi

# ── 6) 환경변수 힌트 출력 ───────────────────────────────────────────────────
cat <<EOF

────────────────────────────────────────────────────────────────────────
Distill 툴체인 설치 완료.

다음 환경변수를 쉘 rc 에 추가해 src/distill/quantizer.py 가 찾을 수 있게 하세요:

    export DISTILL_CONVERT_SCRIPT="$CONVERT_SCRIPT"
    export DISTILL_QUANTIZE_BIN="$QUANTIZE_BIN"

또는 \`.env\` 파일 / direnv 에 추가해도 됩니다.

빠른 검증 (optional):
    \$DISTILL_QUANTIZE_BIN --help | head -5

재실행이 필요한 경우:
    make setup-distill-toolchain

업스트림 llama.cpp 에 새 아키텍처 지원이 추가되면 이 스크립트 재실행만으로
convert + quantize + libllama 가 한 번에 매칭된 최신 버전으로 갱신됩니다.
────────────────────────────────────────────────────────────────────────
EOF
