# Zero-Downtime Migration Patterns

운영 중인 서비스에서 스키마 변경 시 **다운타임 없이** 적용하는 패턴 모음.
PR 마다 `migrations/versions/*.py` 가 이 가이드의 패턴 중 하나를 따르는지 검토.

자동 검사: `make db-check FILE=migrations/versions/0002_xxx.py`

## 핵심 원칙

> **"새 구조와 기존 구조가 동시에 동작하는 중간 상태가 반드시 존재해야 한다."**

- App N 버전이 schema N+1 에서 동작 OK → app N+1 배포 가능
- App N+1 이 schema N+1 의 신기능 사용 → 안전하게 schema N 정리 가능

## 위험 변경 vs 안전 변경

### ✅ 안전 (한 번에 OK)

- `ADD COLUMN ... NULL` (기본값 없음 또는 default 존재)
- 새 테이블 생성
- 새 인덱스 (`CREATE INDEX CONCURRENTLY`)
- 새 enum 값 추가 (PostgreSQL `ALTER TYPE ... ADD VALUE`)
- VARCHAR 길이 늘리기 (PG 9.2+)

### ⚠️ 위험 (반드시 2~3단계 분리)

- `ADD COLUMN ... NOT NULL` ← 기본값 없는 컬럼은 기존 행 모두 update 필요
- `DROP COLUMN`
- `RENAME COLUMN` / `RENAME TABLE`
- `ALTER COLUMN TYPE` (incompatible)
- `DROP TABLE`
- 인덱스 추가 (without CONCURRENTLY) — 큰 테이블에서 lock
- FK constraint 추가
- check constraint 추가

---

## 패턴 1: ADD NOT NULL COLUMN (3-step)

**잘못된 방법 (1-step)** — 기존 행 동안 lock + NOT NULL 위반:

```python
def upgrade():
    op.add_column("users", sa.Column("email", sa.String(255), nullable=False))  # ❌
```

**올바른 방법 (3-step)**:

```python
# Migration 0002 — Step 1: nullable 컬럼 추가 + default
def upgrade():
    op.add_column("users", sa.Column("email", sa.String(255), nullable=True))
    op.execute("UPDATE users SET email = '' WHERE email IS NULL")  # backfill
```

```python
# Migration 0003 — Step 2: NOT NULL 강제
def upgrade():
    op.alter_column("users", "email", nullable=False)
```

App 코드는 0002 적용 직후 새 컬럼 사용 시작, 0003 은 모든 인스턴스가
새 코드 배포 후 적용.

---

## 패턴 2: DROP COLUMN (2-step)

**잘못된 방법** — 구버전 app 이 컬럼 SELECT 시 에러:

```python
def upgrade():
    op.drop_column("users", "old_field")  # ❌ 즉시
```

**올바른 방법**:

1. **App 코드**: `old_field` 참조 모두 제거 + 배포 (모든 인스턴스가 새 코드 실행 확인)
2. **Migration**:
   ```python
   def upgrade():
       op.drop_column("users", "old_field")
   ```

DROP 은 **마지막 인스턴스가 새 코드로 교체된 후** 적용.

---

## 패턴 3: RENAME COLUMN (3-step) — Expand-Contract

**잘못된 방법** — 구버전 app SELECT 깨짐:

```python
def upgrade():
    op.alter_column("users", "username", new_column_name="user_name")  # ❌
```

**올바른 방법 (Expand-Contract)**:

```python
# Migration 0002 — Expand: 새 컬럼 추가 + 동기화 트리거 (선택)
def upgrade():
    op.add_column("users", sa.Column("user_name", sa.String(255), nullable=True))
    op.execute("UPDATE users SET user_name = username")
    # PG trigger 로 dual-write 자동화 가능
    op.execute("""
        CREATE OR REPLACE FUNCTION sync_user_name() RETURNS trigger AS $$
        BEGIN
          NEW.user_name := COALESCE(NEW.user_name, NEW.username);
          NEW.username := COALESCE(NEW.username, NEW.user_name);
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER sync_user_name_t BEFORE INSERT OR UPDATE ON users
          FOR EACH ROW EXECUTE FUNCTION sync_user_name();
    """)
```

```python
# App: 신·구 버전 모두 user_name OR username 읽기 가능 (dual-write trigger 덕분)
# 신 버전 배포 → 구 버전 모두 종료 확인
```

```python
# Migration 0003 — Contract: 트리거 + 구 컬럼 제거
def upgrade():
    op.execute("DROP TRIGGER IF EXISTS sync_user_name_t ON users")
    op.execute("DROP FUNCTION IF EXISTS sync_user_name()")
    op.drop_column("users", "username")
```

---

## 패턴 4: 큰 테이블 인덱스 — CONCURRENTLY

**잘못된 방법** — write lock:

```python
def upgrade():
    op.create_index("idx_users_email", "users", ["email"])  # ❌ 큰 테이블에서 lock
```

**올바른 방법** (PostgreSQL — Alembic 의 batch-mode 우회):

```python
def upgrade():
    # transaction 밖에서 실행되어야 함
    with op.get_context().autocommit_block():
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_users_email ON users(email)")
```

`op.create_index(..., postgresql_concurrently=True)` 도 동일 효과.

---

## 패턴 5: ALTER COLUMN TYPE (incompatible) — 3-step

VARCHAR(50) → VARCHAR(20) 같은 truncating 변경:

1. **Migration A**: 새 컬럼 `email_v2 VARCHAR(20)` 추가 + 데이터 마이그레이션 + dual-write
2. **App N+1 배포**: `email_v2` 만 사용
3. **Migration B**: `email` 제거 + `email_v2` 를 `email` 로 RENAME (다시 패턴 3)

---

## 패턴 6: FK constraint 추가 — NOT VALID

큰 테이블에 FK 추가 시 lock 시간 단축:

```python
def upgrade():
    op.execute("""
        ALTER TABLE orders
        ADD CONSTRAINT fk_orders_user
        FOREIGN KEY (user_id) REFERENCES users(id) NOT VALID
    """)
    # 별도로, 백그라운드에서 검증
    op.execute("ALTER TABLE orders VALIDATE CONSTRAINT fk_orders_user")
```

---

## 패턴 7: 백필이 큰 데이터 — 청크 단위

```python
def upgrade():
    op.add_column("logs", sa.Column("normalized_at", sa.DateTime(), nullable=True))
    # 단일 UPDATE 는 lock 시간 김 → 배치
    op.execute("""
        DO $$
        DECLARE batch_size INT := 10000;
        BEGIN
          LOOP
            UPDATE logs SET normalized_at = created_at
            WHERE id IN (
              SELECT id FROM logs WHERE normalized_at IS NULL LIMIT batch_size
            );
            EXIT WHEN NOT FOUND;
            COMMIT;
            PERFORM pg_sleep(0.1);  -- vacuum 친화적
          END LOOP;
        END $$;
    """)
```

---

## 사전 체크리스트 (PR review)

- [ ] 위험 변경이면 2-step 이상 분리됐나?
- [ ] App 코드 변경과 schema 변경의 deploy 순서 명시됐나? (`# Apply: app v1.5+ 배포 후`)
- [ ] 큰 테이블 인덱스는 `CONCURRENTLY` 인가?
- [ ] downgrade() 가 의미 있나? (위험 변경은 downgrade 불가 명시)
- [ ] 백필은 LOG 또는 batch 단위?
- [ ] `make db-check FILE=...` 통과?

---

## 참고

- [PostgreSQL Schema Changes Without Pain](https://www.braintreepayments.com/blog/safe-operations-for-high-volume-postgresql/)
- [Strong Migrations (Rails)](https://github.com/ankane/strong_migrations#dangerous-operations) — 위험 패턴 카탈로그
- 자체 검사: `scripts/db_migration_check.py`
