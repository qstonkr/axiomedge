#!/bin/bash
# 새 worktree 생성 스크립트 — Claude CLI 병렬 작업용
#
# Usage:
#   ./scripts/new-worktree.sh search "검색 품질 개선"
#   ./scripts/new-worktree.sh pipeline "OCR 파이프라인 수정"
#   ./scripts/new-worktree.sh frontend "대시보드 UI 개선"
#
# 생성 후:
#   cd ../knowledge-local-<name>
#   claude  # Claude CLI 실행

set -e

NAME=${1:?"Usage: $0 <name> [description]"}
DESC=${2:-"$NAME 작업"}
BRANCH="agent/${NAME}"
WORKTREE_DIR="../knowledge-local-${NAME}"
BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$BASE_DIR"

# main 최신 상태로
git fetch origin main 2>/dev/null || true

# worktree 생성
if [ -d "$WORKTREE_DIR" ]; then
    echo "⚠️  Worktree already exists: $WORKTREE_DIR"
    echo "   cd $WORKTREE_DIR && claude"
    exit 0
fi

git worktree add "$WORKTREE_DIR" -b "$BRANCH" origin/main 2>/dev/null || \
git worktree add "$WORKTREE_DIR" -b "$BRANCH" main

echo ""
echo "✅ Worktree 생성 완료!"
echo ""
echo "   브랜치: $BRANCH"
echo "   경로:   $WORKTREE_DIR"
echo "   설명:   $DESC"
echo ""
echo "📌 다음 단계:"
echo "   cd $WORKTREE_DIR"
echo "   claude                          # Claude CLI 실행"
echo ""
echo "📌 작업 완료 후:"
echo "   git push -u origin $BRANCH"
echo "   gh pr create --title \"$DESC\""
echo "   cd $BASE_DIR"
echo "   git worktree remove $WORKTREE_DIR"
