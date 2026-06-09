#!/usr/bin/env python3
"""
Translate a generated digest markdown into Japanese using the Anthropic API.

Preserves all markdown structure, URLs, @handles, hashtags, numbers and emoji;
only the human-readable prose and labels are translated.

Usage:
    ANTHROPIC_API_KEY=... python translate_digest.py data/digest.md \
        --output data/digest.ja.md

ANTHROPIC_API_KEY accepts either a standard API key (`sk-ant-api03-…`, sent via
the `x-api-key` header) or an OAuth token (`sk-ant-oat01-…`, sent via
`Authorization: Bearer`). The auth scheme is auto-detected from the key prefix,
so swapping the secret value is enough — no code change needed. Note that OAuth
tokens are short-lived and not auto-refreshed, so they are only a stopgap for
unattended/scheduled runs; prefer a standard API key for the daily workflow.

The document is split into blank-line-delimited blocks (which keeps each tweet
entry and its `[source](...)` line together), batched under a character budget,
and each batch is translated in a single Messages API call. Only the Python
standard library is used so the workflow needs no extra dependencies.
"""

import os
import sys
import json
import time
import argparse
import urllib.request
import urllib.error

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_VERSION = "2023-06-01"

SYSTEM_PROMPT = (
    "あなたはプロの翻訳者です。与えられた Markdown 断片を自然な日本語に翻訳します。\n"
    "厳守事項:\n"
    "- Markdown の構造（見出し #、箇条書き -、強調 **、区切り --- など）をそのまま保持する\n"
    "- URL、リンク先、@ユーザー名、#ハッシュタグ、数値、絵文字は一切変更しない\n"
    "- リンクの表示ラベル \"source\" は \"出典\" に、\"likes\" は \"いいね\" に訳す\n"
    "- ツイート本文・見出し・説明文など、人間が読む文章のみを日本語に翻訳する\n"
    "- 翻訳結果の Markdown だけを返す。前置き・解説・コードフェンス(```)で囲まない\n"
    "- 既に日本語の部分はそのまま残す"
)


def auth_headers(api_key: str) -> dict[str, str]:
    """Pick the auth header by key type.

    OAuth tokens (`sk-ant-oat01-…`) must use `Authorization: Bearer`; standard
    API keys (`sk-ant-api03-…`) use `x-api-key`. Auto-detecting by prefix means
    swapping the secret value is enough to switch schemes — no code change.
    """
    if api_key.startswith("sk-ant-oat"):
        return {"authorization": f"Bearer {api_key}"}
    return {"x-api-key": api_key}


def split_blocks(text: str) -> list[str]:
    """Blank-line-delimited blocks keep each tweet entry intact."""
    return text.split("\n\n")


def batch_blocks(blocks: list[str], max_chars: int) -> list[list[str]]:
    batches: list[list[str]] = []
    cur: list[str] = []
    cur_len = 0
    for b in blocks:
        add = len(b) + 2
        if cur and cur_len + add > max_chars:
            batches.append(cur)
            cur, cur_len = [], 0
        cur.append(b)
        cur_len += add
    if cur:
        batches.append(cur)
    return batches


def call_api(api_key: str, model: str, content: str, max_tokens: int, retries: int = 4) -> str:
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": content}],
    }).encode("utf-8")
    headers = {
        **auth_headers(api_key),
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(API_URL, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
            return "".join(parts).strip()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            last_err = f"HTTP {e.code}: {detail}"
            # Retry on rate limit / transient server errors with exponential backoff.
            if e.code in (429, 500, 502, 503, 529):
                time.sleep(2 ** attempt)
                continue
            break
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = str(e)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Anthropic API request failed: {last_err}")


def translate(text: str, api_key: str, model: str, max_chars: int, max_tokens: int) -> str:
    batches = batch_blocks(split_blocks(text), max_chars)
    out: list[str] = []
    for i, batch in enumerate(batches):
        fragment = "\n\n".join(batch)
        instruction = "次の Markdown 断片を、上記ルールに従って日本語に翻訳してください:\n\n" + fragment
        out.append(call_api(api_key, model, instruction, max_tokens))
        print(f"  translated batch {i + 1}/{len(batches)} ({len(fragment)} chars)", file=sys.stderr)
    return "\n\n".join(out) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description="Translate a digest markdown to Japanese via the Anthropic API")
    p.add_argument("input", help="Input markdown file")
    p.add_argument("--output", "-o", default=None, help="Output markdown file (default: <input>.ja.md)")
    p.add_argument("--model", default=os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL))
    p.add_argument("--max-chars", type=int, default=4000, help="Max input chars per API batch")
    p.add_argument("--max-tokens", type=int, default=4096, help="Max output tokens per batch")
    args = p.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 1
    api_key = api_key.strip()

    with open(args.input, encoding="utf-8") as f:
        text = f.read()
    if not text.strip():
        print("Error: input digest is empty", file=sys.stderr)
        return 1

    output = args.output or (args.input.rsplit(".", 1)[0] + ".ja.md")
    translated = translate(text, api_key, args.model, args.max_chars, args.max_tokens)
    with open(output, "w", encoding="utf-8") as f:
        f.write(translated)
    print(f"Saved Japanese digest to {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
