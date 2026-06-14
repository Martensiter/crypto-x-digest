#!/bin/bash
# Claude Code Stop hook (repo-scoped):
# 作業ツリー + index + 生成した .claude-resume.md を wip/checkpoint/<branch>
# へ force push (非破壊)。Routine 側はこのブランチを検出して reset --hard で
# 復元する。

set -e

hook_input=$(cat 2>/dev/null || printf '{}')

cd "$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0

branch=$(git branch --show-current 2>/dev/null)
[ -z "$branch" ] && exit 0

case "$branch" in
  wip/checkpoint/*) exit 0 ;;
esac

head=$(git rev-parse HEAD 2>/dev/null || true)
[ -z "$head" ] && exit 0

# 作業ツリーがクリーン & HEAD が origin と同じならスキップ + 古い wip を掃除
if git diff --quiet 2>/dev/null && git diff --cached --quiet 2>/dev/null && \
   [ -z "$(git ls-files --others --exclude-standard 2>/dev/null)" ]; then
  origin_sha=$(git rev-parse "origin/$branch" 2>/dev/null || true)
  if [ -n "$origin_sha" ] && [ "$origin_sha" = "$head" ]; then
    git push --delete origin "wip/checkpoint/$branch" >/dev/null 2>&1 || true
    exit 0
  fi
fi

transcript=$(printf '%s' "$hook_input" | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("transcript_path",""))
except Exception:
    pass' 2>/dev/null || true)

last_msg=""
if [ -n "$transcript" ] && [ -f "$transcript" ]; then
  last_msg=$(python3 - "$transcript" <<'PY' 2>/dev/null || true
import json, sys
last = ""
try:
    with open(sys.argv[1]) as f:
        for line in f:
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if entry.get("type") == "assistant":
                content = entry.get("message", {}).get("content", [])
                parts = [c.get("text", "") for c in content
                         if isinstance(c, dict) and c.get("type") == "text"]
                text = "\n".join(parts).strip()
                if text:
                    last = text
except Exception:
    pass
print(last[:800])
PY
)
fi

now=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
last_commit=$(git log -1 --oneline 2>/dev/null || true)
has_changes="no"
if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
  has_changes="yes"
fi

repo_root="$(git rev-parse --show-toplevel)"
resume_file="$repo_root/.claude-resume.md"

cat > "$resume_file" <<EOF
updated_at: $now
branch: $branch
checkpoint: wip/checkpoint/$branch
last_commit: $last_commit
has_uncommitted_changes: $has_changes

## last_assistant_message

$last_msg
EOF

tmpidx=$(mktemp -t claude-idx.XXXXXX)
cleanup() { rm -f "$tmpidx" "$resume_file"; }
trap cleanup EXIT

real_index="$(git rev-parse --git-path index)"
[ -f "$real_index" ] && cp "$real_index" "$tmpidx"

export GIT_INDEX_FILE="$tmpidx"
git add -A >/dev/null 2>&1 || true
git add -f "$resume_file" >/dev/null 2>&1 || true
tree=$(git write-tree 2>/dev/null || true)
unset GIT_INDEX_FILE

[ -z "$tree" ] && exit 0

commit=$(printf 'wip: auto-checkpoint %s\n' "$now" | git commit-tree "$tree" -p "$head" 2>/dev/null || true)
[ -z "$commit" ] && exit 0

git push --force origin "$commit:refs/heads/wip/checkpoint/$branch" >/dev/null 2>&1 || true
