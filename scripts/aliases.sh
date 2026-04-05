#!/bin/bash
# Claude CLI 병렬 작업 alias 모음
#
# 설치: echo 'source ~/projects/gs/knowledge-local/scripts/aliases.sh' >> ~/.zshrc
#
# 사용:
#   kl-new search "검색 개선"     → worktree 생성 + Claude 실행
#   kl-new pipeline "OCR 수정"    → worktree 생성 + Claude 실행
#   kl-pr "검색 품질 개선"         → push + PR 생성
#   kl-done search                → worktree 정리
#   kl-list                       → 현재 활성 worktree 목록

KL_BASE="$HOME/projects/gs/knowledge-local"

# 새 작업 시작 (worktree + Claude CLI)
kl-new() {
    local name=${1:?"Usage: kl-new <name> [description]"}
    local desc=${2:-"$name 작업"}
    local dir="$KL_BASE/../knowledge-local-${name}"

    cd "$KL_BASE"
    git fetch origin main 2>/dev/null

    if [ -d "$dir" ]; then
        echo "이미 존재: $dir"
        cd "$dir"
    else
        git worktree add "$dir" -b "agent/${name}" origin/main 2>/dev/null || \
        git worktree add "$dir" -b "agent/${name}" main
        cd "$dir"
        echo "✅ worktree 생성: agent/${name}"
    fi

    echo "🚀 Claude CLI 시작..."
    claude
}

# PR 생성
kl-pr() {
    local title=${1:?"Usage: kl-pr <title>"}
    local branch=$(git branch --show-current)
    git push -u origin "$branch"
    gh pr create --title "$title"
}

# 작업 정리 (worktree 삭제)
kl-done() {
    local name=${1:?"Usage: kl-done <name>"}
    local dir="$KL_BASE/../knowledge-local-${name}"
    cd "$KL_BASE"
    git worktree remove "$dir" 2>/dev/null && echo "✅ 정리 완료: $name" || echo "❌ 실패: $dir"
}

# 활성 worktree 목록
kl-list() {
    cd "$KL_BASE"
    git worktree list
}

# 메인으로 돌아가기
kl-home() {
    cd "$KL_BASE"
}
