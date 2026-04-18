# Secret Management — SOPS + age

## 왜 SOPS

- **평문 .env 금지** — git, 백업, 로그 어디든 유출 위험
- **Vault/AWS SecretsManager 보다 가벼움** — 별도 서버 불필요
- **K8s/Helm 통합 쉬움** — `helm-secrets` plugin 또는 sops-driver
- **age 키** = 짧은 ed25519 기반 (PGP 보다 단순)

## 1회 setup

```bash
# 1. age + SOPS 설치
brew install age sops          # macOS
# 또는: https://github.com/getsops/sops/releases

# 2. 개인 age 키 생성
mkdir -p ~/.config/sops/age
age-keygen -o ~/.config/sops/age/keys.txt
chmod 600 ~/.config/sops/age/keys.txt

# 3. 공개키 확인
grep "public key" ~/.config/sops/age/keys.txt
# → age1abc...xyz

# 4. .sops.yaml 의 <YOUR_AGE_PUBLIC_KEY> 를 위 값으로 교체 (커밋)

# 5. 환경변수 등록 (~/.zshrc 또는 ~/.bashrc)
echo 'export SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt' >> ~/.zshrc
```

## 일상 워크플로우

```bash
# 평문 .env 수정 → 암호화
make secrets-encrypt              # .env → .env.encrypted (git에 커밋)

# .env.encrypted → 평문 (로컬 dev 시)
make secrets-decrypt              # .env.encrypted → .env (gitignore — 커밋 X)

# 암호화 상태 검증 (CI 게이트)
make secrets-check                # .env.encrypted 유효한 SOPS 파일인지 검증
```

## 팀 환경

여러 사람이 같은 secrets 에 접근하려면 `.sops.yaml` 의 `age:` 항목에
각자 공개키를 추가:

```yaml
age:
  - age1<alice-pubkey>
  - age1<bob-pubkey>
  - age1<carol-pubkey>
```

추가 후 한 번 `sops updatekeys .env.encrypted` 실행하면 신규 멤버가 복호화 가능.

키 회전 (멤버 이탈):
1. `.sops.yaml` 에서 해당 공개키 제거
2. **모든 secret 값을 새로 발급** (이미 본 사람은 영원히 평문 보관 가능)
3. `sops updatekeys .env.encrypted` 로 SOPS 메타데이터 정리

## CI 통합

GitHub Actions:

```yaml
- name: Decrypt secrets
  env:
    SOPS_AGE_KEY: ${{ secrets.SOPS_AGE_KEY }}  # GitHub secret 에 age 비밀키 저장
  run: |
    mkdir -p ~/.config/sops/age
    echo "$SOPS_AGE_KEY" > ~/.config/sops/age/keys.txt
    sops -d .env.encrypted > .env
```

## K8s / Helm

`deploy/helm/` 에 secret manifest 추가 시:
1. `secrets.enc.yaml` 로 명명 → SOPS 가 자동 암호화
2. `helm secrets install ...` (helm-secrets plugin) 으로 배포
3. 또는 ArgoCD + sops-driver 패턴

## 주의

- `.env.encrypted` 만 커밋. `.env` (평문) 는 `.gitignore` 에.
- age 비밀키 (`keys.txt`) 절대 커밋 X.
- CI 의 SOPS_AGE_KEY 도 GitHub Secrets / Vault 에만.
