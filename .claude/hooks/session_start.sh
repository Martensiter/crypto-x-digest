#!/bin/bash
# Claude Code SessionStart フック
# セッション開始時に未完了タスクを表示し、新タスクの自動登録を促す

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
TASKS_FILE="$REPO_ROOT/.claude/tasks.json"
MANAGE_SCRIPT="$REPO_ROOT/scripts/claude/manage_tasks.py"

echo "=== タスクキュー ==="

# 未完了タスクを表示
if [ -f "$TASKS_FILE" ]; then
  pending=$(python3 -c "
import json, sys
try:
    tasks = json.load(open('$TASKS_FILE'))
    pending = [t for t in tasks if t.get('status') in ('pending', 'in_progress')]
    if pending:
        for t in pending:
            icon = '▶' if t['status'] == 'in_progress' else '○'
            branch = f\" [{t['branch']}]\" if t.get('branch') else ''
            print(f\"  {icon} [{t['id']}] {t['description']}{branch}\")
except Exception:
    pass
" 2>/dev/null)

  if [ -n "$pending" ]; then
    echo "未完了タスク:"
    echo "$pending"
    echo ""
    echo "→ 上記タスクを継続してください"
    echo "→ 完了後: python $MANAGE_SCRIPT complete <id>"
  else
    echo "未完了タスクなし"
  fi
else
  echo "未完了タスクなし"
fi

echo ""
echo "【自動登録ルール】"
echo "ユーザーから新しいタスクを受けたら、作業開始前に自動登録すること:"
echo "  python $MANAGE_SCRIPT add \"タスクの説明\""
echo "作業完了時:"
echo "  python $MANAGE_SCRIPT complete <id>"
