#!/usr/bin/env python3
"""
Claude Code 用タスクキュー管理。

セッションをまたぐタスクの引き継ぎ用キュー。タスクは .claude/tasks.json に保存され、
SessionStart フック (.claude/hooks/session_start.sh) が未完了タスクを表示する。

Usage:
  # タスクを追加
  python scripts/claude/manage_tasks.py add "サムネイルの A/B テストを最適化する"

  # タスクを追加（ブランチ指定）
  python scripts/claude/manage_tasks.py add "バグ修正" --branch claude/fix-bug-xyz

  # 未処理タスクを表示
  python scripts/claude/manage_tasks.py list

  # 最も古い未処理タスクを取得（CI 用）
  python scripts/claude/manage_tasks.py next

  # タスクを完了にする
  python scripts/claude/manage_tasks.py complete <task_id>

  # 完了済みタスクをクリア
  python scripts/claude/manage_tasks.py clean
"""
import argparse
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

TASKS_FILE = Path(__file__).resolve().parents[2] / ".claude" / "tasks.json"


def load_tasks() -> list[dict]:
    if not TASKS_FILE.exists():
        return []
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_tasks(tasks: list[dict]):
    TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)


def add_task(description: str, branch: str = "", context: str = ""):
    tasks = load_tasks()
    task = {
        "id": uuid.uuid4().hex[:8],
        "description": description,
        "branch": branch,
        "context": context,
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
        "completed_at": None,
    }
    tasks.append(task)
    save_tasks(tasks)
    print(f"タスク追加: [{task['id']}] {description}")
    if branch:
        print(f"  ブランチ: {branch}")
    return task


def list_tasks(status_filter: str = ""):
    tasks = load_tasks()
    if status_filter:
        tasks = [t for t in tasks if t["status"] == status_filter]
    if not tasks:
        print("タスクなし")
        return
    for t in tasks:
        icon = {"pending": "○", "in_progress": "▶", "completed": "✓", "failed": "✗"}.get(
            t["status"], "?"
        )
        branch_str = f" [{t['branch']}]" if t.get("branch") else ""
        print(f"  {icon} [{t['id']}] {t['description']}{branch_str} ({t['status']})")


def get_next_task() -> dict | None:
    tasks = load_tasks()
    pending = [t for t in tasks if t["status"] == "pending"]
    if not pending:
        return None
    # 最も古いタスクを返す
    task = pending[0]
    # in_progress に更新
    for t in tasks:
        if t["id"] == task["id"]:
            t["status"] = "in_progress"
            t["started_at"] = datetime.utcnow().isoformat()
    save_tasks(tasks)
    return task


def complete_task(task_id: str, success: bool = True):
    tasks = load_tasks()
    found = False
    for t in tasks:
        if t["id"] == task_id:
            t["status"] = "completed" if success else "failed"
            t["completed_at"] = datetime.utcnow().isoformat()
            found = True
            print(f"タスク{'完了' if success else '失敗'}: [{task_id}] {t['description']}")
            break
    if not found:
        print(f"タスク {task_id} が見つかりません")
        sys.exit(1)
    save_tasks(tasks)


def clean_tasks():
    tasks = load_tasks()
    before = len(tasks)
    tasks = [t for t in tasks if t["status"] not in ("completed",)]
    save_tasks(tasks)
    removed = before - len(tasks)
    print(f"完了済みタスク {removed} 件を削除")


def main():
    parser = argparse.ArgumentParser(description="Claude Code タスクキュー管理")
    sub = parser.add_subparsers(dest="command")

    p_add = sub.add_parser("add", help="タスクを追加")
    p_add.add_argument("description", help="タスクの説明")
    p_add.add_argument("--branch", default="", help="作業ブランチ")
    p_add.add_argument("--context", default="", help="追加コンテキスト")

    sub.add_parser("list", help="タスク一覧")

    p_next = sub.add_parser("next", help="次の未処理タスクを取得（CI 用）")
    p_next.add_argument("--json", action="store_true", help="JSON で出力")

    p_complete = sub.add_parser("complete", help="タスクを完了にする")
    p_complete.add_argument("task_id", help="タスクID")
    p_complete.add_argument("--failed", action="store_true", help="失敗として記録")

    sub.add_parser("clean", help="完了済みタスクをクリア")

    args = parser.parse_args()

    if args.command == "add":
        add_task(args.description, branch=args.branch, context=args.context)
    elif args.command == "list":
        list_tasks()
    elif args.command == "next":
        task = get_next_task()
        if task:
            if getattr(args, "json", False):
                print(json.dumps(task, ensure_ascii=False))
            else:
                print(f"[{task['id']}] {task['description']}")
                if task.get("branch"):
                    print(f"branch={task['branch']}")
        else:
            print("pending タスクなし")
            sys.exit(1)
    elif args.command == "complete":
        complete_task(args.task_id, success=not args.failed)
    elif args.command == "clean":
        clean_tasks()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
