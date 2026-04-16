# Development Guide

코드 컨벤션, async 패턴, 계층 구조, 테스트 작성법.
`CONTRIBUTING.md` 의 기본 설정 후 이 문서로 심화.

---

## 계층 구조

```
Route handler (thin) → Helper (business logic) → Repository (DB) → Model (ORM)
                                                → Service (orchestration)
```

| 계층 | 역할 | 파일 위치 |
|---|---|---|
| **Route** | HTTP 요청/응답만. 검증은 Pydantic. | `src/api/routes/*.py` |
| **Helper** | 비즈니스 로직 (검색 pipeline step 등) | `src/api/routes/*_helpers.py` |
| **Service** | 여러 repo/provider 조합 오케스트레이션 | `src/distill/service.py`, `src/search/*.py` |
| **Repository** | DB 접근만. 단순 CRUD + 복잡 쿼리. | `src/database/repositories/*.py`, `src/distill/repositories/*.py` |
| **Provider** | 외부 서비스 래퍼 (LLM, embedding, OCR) | `src/llm/*.py`, `src/embedding/*.py`, `src/providers/*.py` |

### 의존성 방향

```
Route → Helper → Service → Repository → Model
                         → Provider
```

**역방향 금지**: Repository 가 Route 를 import 하면 안 됨.

---

## Async 패턴

### 모든 API 는 async def

```python
@router.get("/items")
async def list_items():
    repo = _get_repo()
    return await repo.list_all()
```

### CPU-bound → `asyncio.to_thread()`

```python
# OCR, 임베딩 인코딩, 파일 파싱 등 CPU 집약 작업
result = await asyncio.to_thread(heavy_sync_function, arg1, arg2)
```

### 동시성 제어

```python
# Semaphore 로 외부 API 동시 호출 제한
self._semaphore = asyncio.Semaphore(4)

async with self._semaphore:
    result = await external_api.call(...)
```

### 병렬 실행

```python
# 독립적인 N 개 작업을 한 번에
results = await asyncio.gather(
    task_a(),
    task_b(),
    task_c(),
    return_exceptions=False,  # 하나 실패하면 전체 실패
)
```

---

## Repository 패턴

### 기본 구조

```python
class MyRepository:
    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._session_maker = session_maker

    async def get(self, id: str) -> dict | None:
        async with self._session_maker() as session:
            stmt = select(MyModel).where(MyModel.id == id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._to_dict(row) if row else None

    @staticmethod
    def _to_dict(model: MyModel) -> dict:
        return {"id": model.id, "name": model.name, ...}
```

### 규칙

- **async 필수**: `AsyncSession` 만 사용
- **session context manager**: `async with self._session_maker() as session:`
- **commit 범위 명확**: 쓰기 작업은 같은 session 에서 commit
- **_to_dict 분리**: ORM → dict 변환은 static method
- **API route 에서 직접 SQL 금지**: 반드시 repository 경유

---

## Pydantic 모델

### Request / Response 분리

```python
# Request — 클라이언트가 보내는 것
class ProfileCreateRequest(BaseModel):
    name: str = Field(..., max_length=100)
    base_model: str = Field(..., min_length=1, max_length=200)

# 내부 모델 (DB → dict → response) 은 별도 class 불필요 — dict 반환이 표준.
```

### Field 검증

```python
class BuildTriggerRequest(BaseModel):
    steps: list[str] | None = None

    @field_validator("steps")
    @classmethod
    def validate_steps(cls, v):
        if v is not None:
            unknown = set(v) - VALID_STEPS
            if unknown:
                raise ValueError(f"Unknown steps: {unknown}")
        return v
```

### 타입 구체화 (Phase C 과제)

```python
# 나쁨 — 구조 불명
lora: dict | None = None

# 좋음 — 검증 가능
lora: LoRAConfig | None = None
```

---

## 에러 처리

### 금지

```python
# ❌ bare except
except Exception:
    pass

# ❌ 로깅 없는 fallback
except Exception:
    return default_value
```

### 권장

```python
# ✅ 구체적 예외 + 로깅
except httpx.TimeoutException as e:
    logger.warning("TEI timeout: %s", e)
    return fallback

# ✅ bare except 쓰더라도 반드시 로깅
except Exception as e:
    logger.warning("Unexpected error in health check: %s", e)
    return False
```

### HTTP 예외

```python
from fastapi import HTTPException

raise HTTPException(status_code=400, detail="Profile not found")
raise HTTPException(status_code=503, detail="Distill service not initialized")
```

---

## Pipeline Stage 패턴

PR10 에서 도입. 신규 pipeline 단계 추가 시 따르는 패턴:

```python
from src.distill.pipeline.stages import DataGenContext, DataGenStage

class MyNewStage:
    name = "my_stage"

    def __init__(self, dependency):
        self._dep = dependency

    async def process(self, ctx: DataGenContext) -> DataGenContext:
        # ctx.rows 변형
        for row in ctx.rows:
            row["new_field"] = await self._dep.compute(row)

        # 로그 기록
        ctx.stage_logs[self.name] = {"processed": len(ctx.rows)}
        return ctx
```

Pipeline 조립 (`service.py`):
```python
pipeline = DataGenPipeline(ctx).add(MyNewStage(dep))
result = await pipeline.run()
```

---

## Provider Registry 패턴

PR8 에서 도입. 새 LLM/Auth provider 추가 시:

```python
from src.providers.llm import register_llm_provider

@register_llm_provider("claude")
def _create_claude(settings):
    from my_pkg import ClaudeClient
    return ClaudeClient(api_key=settings.anthropic.api_key)
```

호출자:
```python
from src.providers import create_llm_client
client = create_llm_client("claude", settings=settings)
```

---

## 참고

- 테스트 작성: `docs/TESTING.md`
- 코드 컨벤션 (lint): `pyproject.toml::[tool.ruff]`
- Git workflow: `CONTRIBUTING.md`
- 아키텍처: `docs/ARCHITECTURE.md`
- 개선 계획: `docs/IMPROVEMENT_PLAN.md`
