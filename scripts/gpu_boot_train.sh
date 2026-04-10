#!/bin/bash
# GPU EC2 부팅 시 자동 실행 — S3에서 학습 작업 감지 → 학습 → 결과 업로드 → shutdown
#
# systemd 서비스로 등록:
#   /etc/systemd/system/distill-train.service
#   [Service]
#   Type=oneshot
#   ExecStart=/opt/distill/gpu_boot_train.sh
#   [Install]
#   WantedBy=multi-user.target
#
# S3 구조:
#   s3://{bucket}/{profile}/train/{build_id}/
#     ├── train.jsonl     ← 학습 데이터
#     ├── config.json     ← 모델, 하이퍼파라미터
#     └── output/         ← 학습 완료 시 생성
#           ├── result.json
#           ├── model_merged/
#           └── model.gguf

set -euo pipefail

BUCKET="${DISTILL_S3_BUCKET:-gs-knowledge-models}"
REGION="${AWS_REGION:-ap-northeast-2}"
WORK_DIR="/opt/distill"
LOG_FILE="/var/log/distill-train.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG_FILE"; }

log "=== GPU Boot Train Started ==="

# S3에서 모든 프로필의 미완료 작업 탐색
PROFILES=$(aws s3 ls "s3://${BUCKET}/" --region "$REGION" | awk '{print $NF}' | tr -d '/')

JOBS_DONE=0
for PROFILE in $PROFILES; do
    # train/ 하위의 build_id 폴더 탐색
    BUILD_IDS=$(aws s3 ls "s3://${BUCKET}/${PROFILE}/train/" --region "$REGION" 2>/dev/null \
        | awk '{print $NF}' | tr -d '/' || true)

    for BUILD_ID in $BUILD_IDS; do
        TRAIN_PATH="s3://${BUCKET}/${PROFILE}/train/${BUILD_ID}"

        # output/result.json 있으면 이미 완료 → 스킵
        if aws s3 ls "${TRAIN_PATH}/output/result.json" --region "$REGION" &>/dev/null; then
            log "SKIP: ${PROFILE}/${BUILD_ID} (already completed)"
            continue
        fi

        # train.jsonl 있어야 유효한 작업
        if ! aws s3 ls "${TRAIN_PATH}/train.jsonl" --region "$REGION" &>/dev/null; then
            log "SKIP: ${PROFILE}/${BUILD_ID} (no train.jsonl)"
            continue
        fi

        log "=== Training: ${PROFILE}/${BUILD_ID} ==="

        # 작업 디렉토리 준비
        JOB_DIR="${WORK_DIR}/jobs/${BUILD_ID}"
        mkdir -p "${JOB_DIR}/output"

        # S3에서 학습 데이터 다운로드
        log "Downloading training data..."
        aws s3 sync "${TRAIN_PATH}/" "${JOB_DIR}/" --region "$REGION" --exclude "output/*"

        # config.json 읽기
        CONFIG_FILE="${JOB_DIR}/config.json"
        if [ ! -f "$CONFIG_FILE" ]; then
            log "ERROR: config.json not found"
            echo '{"status":"failed","error":"config.json not found"}' > "${JOB_DIR}/output/result.json"
            aws s3 cp "${JOB_DIR}/output/result.json" "${TRAIN_PATH}/output/result.json" --region "$REGION"
            continue
        fi

        # 학습 실행
        log "Starting training..."
        cd "$WORK_DIR"

        TRAIN_START=$(date +%s)
        if python3 train_on_gpu.py \
            --data-dir "${JOB_DIR}" \
            --output-dir "${JOB_DIR}/output" \
            --build-id "${BUILD_ID}" \
            2>&1 | tee -a "$LOG_FILE"; then

            TRAIN_END=$(date +%s)
            DURATION=$((TRAIN_END - TRAIN_START))
            log "Training completed in ${DURATION}s"

            # 결과 S3 업로드
            log "Uploading results..."
            aws s3 sync "${JOB_DIR}/output/" "${TRAIN_PATH}/output/" --region "$REGION"
            JOBS_DONE=$((JOBS_DONE + 1))
            log "=== Done: ${PROFILE}/${BUILD_ID} (${DURATION}s) ==="
        else
            log "ERROR: Training failed for ${PROFILE}/${BUILD_ID}"
            echo "{\"status\":\"failed\",\"error\":\"training script exited with error\"}" \
                > "${JOB_DIR}/output/result.json"
            aws s3 cp "${JOB_DIR}/output/result.json" "${TRAIN_PATH}/output/result.json" --region "$REGION"
        fi
    done
done

log "=== All jobs processed: ${JOBS_DONE} completed ==="

# 자동 종료 (비용 절감)
log "Shutting down in 30s..."
sleep 30
sudo shutdown -h now
