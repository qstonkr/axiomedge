# Distill 파이프라인 툴체인

Distill 빌드는 3개 컴포넌트가 **반드시 같은 버전** 에서 나와야 동작합니다.

| 컴포넌트 | 역할 | 요구사항 |
|---|---|---|
| `convert_hf_to_gguf.py` (Python) | HuggingFace 모델 → GGUF f16 변환 | llama.cpp 소스 트리의 Python 스크립트. 새 아키텍처마다 ModelBase 서브클래스 추가됨. |
| `llama-quantize` (C++ 바이너리) | GGUF f16 → Q4_K_M / Q5 등 양자화 | 같은 커밋에서 빌드된 llama.cpp 바이너리. GGUF metadata 키 네이밍이 Python 스크립트와 일치해야 함. |
| `libllama` (C++ 라이브러리) | llama-cpp-python / 엣지 서버의 inference 엔진 | Python wheel (`llama-cpp-python`) 또는 별도 shared object. 양자화된 GGUF 를 로드할 수 있어야 함. |

## 왜 소스 빌드가 필요한가

**Homebrew bottle (stable)** 은 build buffer 때문에 위 3개 컴포넌트 중 일부만 최신이거나 섞인 버전이 되기 쉽습니다. 특히 신규 아키텍처 (EXAONE, Kanana2, Qwen3, Gemma3 등) 가 추가될 때:

- Python convert 스크립트는 PyPI `gguf` 패키지에 의존
- llama-quantize 바이너리는 `libllama` 링크
- 이 둘이 **다른 커밋에서 빌드**되면 GGUF metadata 키 이름 불일치로 `key not found in model: ...` 에러가 납니다.

**실측 사례 (2026-04-16)**: EXAONE 3.5 2.4B 가 아래 순서로 깨졌습니다.

1. Homebrew `convert_hf_to_gguf.py` (llama.cpp 8680 stable) + gguf PyPI 0.18.0 → `gguf.MODEL_ARCH.GEMMA4` AttributeError (convert 스크립트가 gguf 0.19 을 기대)
2. llama.cpp master 에서 빌드한 convert 스크립트 + gguf-py from source → Convert 성공
3. 하지만 Homebrew `llama-quantize` (8680) 는 `exaone.attention.layer_norm_rms_epsilon` 을 찾는데, master Python 스크립트는 `exaone.attention.layer_norm_epsilon` (RMS 없는) 을 씀 → `key not found` 에러
4. llama.cpp master 에서 `llama-quantize` 도 새로 빌드 → **같은 에러** (master 의 Python 스크립트에도 버그 있음)
5. `ExaoneModel.set_gguf_parameters` 에 `add_layer_norm_rms_eps` 호출 패치 추가 → 전체 파이프라인 통과

결론: 1) Python + C++ 둘 다 **같은 커밋** 에서 빌드, 2) 로컬 패치 보존, 3) 재현 가능한 설치 스크립트.

## 설치

```bash
make setup-distill-toolchain
```

이 target 이 하는 일:

1. `$HOME/.cache/knowledge-local/llama.cpp` 에 llama.cpp master 를 clone (또는 `git fetch + reset --hard`)
2. `scripts/patches/*.patch` 를 idempotent 하게 적용 (이미 적용됐으면 skip)
3. cmake Release 빌드 — `llama-quantize`, `llama-cli`, `libllama` 생성
4. uv venv 에 `gguf` 패키지를 **소스에서 editable 설치** (`gguf-py`) — Python 스크립트와 동일 커밋

설치 완료 후 출력되는 환경변수를 쉘 rc (`.zshrc`, `.bashrc`) 에 추가하세요:

```bash
export DISTILL_CONVERT_SCRIPT="$HOME/.cache/knowledge-local/llama.cpp/convert_hf_to_gguf.py"
export DISTILL_QUANTIZE_BIN="$HOME/.cache/knowledge-local/llama.cpp/build/bin/llama-quantize"
```

`src/distill/quantizer.py` 는 이 env var 를 우선 찾습니다. 없으면 에러 + 설치 스크립트 실행 안내.

## 업그레이드 절차

llama.cpp 업스트림에 새 아키텍처 지원이 추가되거나 버그 수정이 머지되면:

```bash
make setup-distill-toolchain
```

같은 명령을 다시 실행하면 됩니다. Idempotent:
- Git repo 가 이미 있으면 `git fetch + reset --hard FETCH_HEAD`
- 로컬 패치가 upstream 에 머지됐으면 `git apply --reverse --check` 로 감지해서 skip
- cmake 증분 빌드

## 로컬 패치 관리

### 현재 유지 중인 패치

| 파일 | 대상 | 설명 |
|---|---|---|
| `scripts/patches/convert_hf_to_gguf_exaone.patch` | `convert_hf_to_gguf.py` `ExaoneModel.set_gguf_parameters` | EXAONE 의 `layer_norm_epsilon` (legacy 명칭, 실제는 RMS) 을 `add_layer_norm_rms_eps` 로 쓰도록 수정. C++ 측이 `LLM_KV_ATTENTION_LAYERNORM_RMS_EPS` 를 기대하는 mismatch 해결. |

### 새 패치 추가

1. `$LLAMA_CPP_SRC` 디렉터리에서 직접 코드 수정하고 로컬 테스트
2. `git diff <file>` 로 unified diff 생성
3. `scripts/patches/<descriptive_name>.patch` 로 저장
4. `make setup-distill-toolchain` 실행해 확인 (이미 적용된 상태라 no-op 이어야 함)
5. 커밋: patch 파일 + 영향받은 아키텍처/모델 설명

### 패치 회수 (upstream 에 머지됐을 때)

1. `make setup-distill-toolchain` 실행 시 `patch does not apply cleanly` 경고 출력
2. Dry-run 으로 관련 모델 재검증
3. 문제 없으면 `scripts/patches/<name>.patch` 삭제 + 이 문서의 "현재 유지 중인 패치" 항목도 제거
4. 커밋

## 검증

설치 직후 빠른 sanity check:

```bash
$DISTILL_QUANTIZE_BIN --help | head -5
# usage: llama-quantize [--help] ...

uv run python -c "import gguf; print(gguf.MODEL_ARCH.GEMMA4)"
# MODEL_ARCH.GEMMA4  (AttributeError 가 나면 venv 의 gguf 가 구버전)
```

전체 모델에 대한 dry-run (convert → quantize → load → Korean query) 은 `/tmp/distill_dryrun/dry_run.py` 같은 one-off 스크립트로 검증. 세션 종료 후 사라지는 ad-hoc 스크립트라 repo 엔 보관하지 않습니다.

## 베이스 모델별 호환성 (2026-04-16 dry-run 결과)

| 모델 | Convert | Quantize | Inference | 한국어 품질 |
|---|---|---|---|---|
| `google/gemma-3-4b-it` | ✅ (multimodal → text tower 자동 추출) | ✅ | ✅ | 정확 |
| `kakaocorp/kanana-nano-2.1b-instruct` | ✅ (LlamaForCausalLM) | ✅ | ✅ | 정확 |
| `LGAI-EXAONE/EXAONE-3.5-2.4B-Instruct` | ⚠ (패치 필요) | ✅ | ✅ | 정확 |
| `naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-1.5B` | ✅ (LlamaForCausalLM) | ✅ | ✅ | 정확 |

`Qwen/Qwen3-4B` 는 파이프라인은 통과하지만 한국어 품질 부족 (기본 `<think>` 모드 + 영어 답변 + 한국사 날짜 오답) 으로 베이스 모델 레지스트리에서 제외.

## 참고 링크

- llama.cpp 업스트림: https://github.com/ggml-org/llama.cpp
- `convert_hf_to_gguf.py` 에서 지원되는 아키텍처: `@ModelBase.register(...)` decorator 검색
- C++ 측 metadata 키 정의: `src/llama-arch.cpp` + `src/llama-model.cpp`
- 관련 내부 노트: Notion "Knowledge RAG Small LM" 페이지 (베이스 모델 정책)
