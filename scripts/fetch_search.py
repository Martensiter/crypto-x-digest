#!/usr/bin/env python3
"""
Fetch tweets via X/Twitter SearchTimeline GraphQL endpoint.

Two streams (either or both):
  - keyword discovery  (--queries-file / --query): "what X considers popular on
    this topic" — e.g. `bitcoin min_faves:1000 filter:links`.
  - curated sources    (--accounts-file): `from:<handle> -filter:replies` for a
    vetted allowlist of industry accounts (media, research, founders, official),
    so the digest is anchored on trusted sources rather than engagement-farmed
    ("impression zombie") noise.

Usage:
    export X_AUTH_TOKEN="..."
    export X_CT0="..."

    python fetch_search.py --query "(BTC OR ETH) min_faves:500 -filter:replies lang:en" --output data/tweets.json
    python fetch_search.py --queries-file config/search_queries.yaml \
        --accounts-file config/source_accounts.yaml --output data/tweets.json

If X rotates the SearchTimeline queryId, override it with X_SEARCH_QUERY_ID env var.
"""

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

AUTH_TOKEN = os.environ.get("X_AUTH_TOKEN", "")
CT0 = os.environ.get("X_CT0", "")

BEARER = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

# X rotates the GraphQL queryId for SearchTimeline. Override via env var if it 404s.
# `or` (not `get(..., default)`) — GHA passes an empty string for an unset secret,
# which would otherwise bypass the fallback and produce qid= in the request URL.
# IMPORTANT: queryId, FEATURES, and `variables` are a MATCHED SET from one x.com
# frontend bundle. On HTTP 400/422 GRAPHQL_VALIDATION_FAILED, re-capture all three
# together from a live SearchTimeline request (DevTools → Network → Payload), not
# just the queryId. Default below + FEATURES + variables were copied 1:1 from a live
# bundle (refresh them together when X changes its schema).
SEARCH_QUERY_ID = os.environ.get("X_SEARCH_QUERY_ID") or "Bcw3RzK-PatNAmbnw54hFw"
ENDPOINT_TEMPLATE = "https://x.com/i/api/graphql/{qid}/SearchTimeline"

# Copied 1:1 from a live x.com SearchTimeline request (DevTools → Network → Payload).
# Must match the queryId above and the `variables` below (same bundle). When X adds/
# removes a feature flag you get HTTP 400/422 GRAPHQL_VALIDATION_FAILED — re-capture.
FEATURES = {
    "rweb_video_screen_enabled": False,
    "rweb_cashtags_enabled": True,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": False,
    "rweb_tipjar_consumption_enabled": False,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "rweb_cashtags_composer_attachment_enabled": True,
    "responsive_web_jetfuel_frame": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "responsive_web_grok_annotations_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "rweb_conversational_replies_downvote_enabled": False,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "content_disclosure_indicator_enabled": True,
    "content_disclosure_ai_generated_indicator_enabled": True,
    "responsive_web_grok_show_grok_translated_post": True,
    "responsive_web_grok_analysis_button_from_backend": True,
    "post_ctas_fetch_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": False,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}


def get_headers(auth_token: str, ct0: str) -> dict:
    return {
        "Authorization": f"Bearer {BEARER}",
        "X-Csrf-Token": ct0,
        "Cookie": f"auth_token={auth_token}; ct0={ct0}",
        "Content-Type": "application/json",
        "X-Twitter-Active-User": "yes",
        "X-Twitter-Auth-Type": "OAuth2Session",
        "X-Twitter-Client-Language": "en",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }


def extract_user(result: dict) -> dict:
    core = result.get("core", {})
    user_results = core.get("user_results", {}).get("result", {})
    user_core = user_results.get("core", {})
    user_legacy = user_results.get("legacy", {})

    if user_core.get("screen_name"):
        return {
            "name": user_core.get("name"),
            "screen_name": user_core.get("screen_name"),
            "verified": user_results.get("is_blue_verified", False),
            "followers": user_legacy.get("followers_count", 0),
        }
    if user_legacy.get("screen_name"):
        return {
            "name": user_legacy.get("name"),
            "screen_name": user_legacy.get("screen_name"),
            "verified": user_results.get("is_blue_verified", False),
            "followers": user_legacy.get("followers_count", 0),
        }
    return {"name": "Unknown", "screen_name": "unknown", "verified": False, "followers": 0}


def _iter_entries(instructions: list) -> list:
    """SearchTimeline returns entries in TimelineAddEntries; some pinned tweets sit in TimelineAddToModule."""
    out = []
    for instruction in instructions:
        itype = instruction.get("type")
        if itype == "TimelineAddEntries":
            out.extend(instruction.get("entries", []))
        elif itype == "TimelineAddToModule":
            for item in instruction.get("moduleItems", []):
                out.append({"content": {"itemContent": item.get("item", {}).get("itemContent", {})}, "entryId": item.get("entryId", "")})
    return out


def _parse_tweet_entry(entry: dict, query_label: str) -> dict | None:
    entry_id = entry.get("entryId", "")
    if not entry_id.startswith("tweet-"):
        return None

    content = entry.get("content", {})
    item_content = content.get("itemContent", {})
    tweet_results = item_content.get("tweet_results", {})
    result = tweet_results.get("result", {})

    if result.get("__typename") == "TweetWithVisibilityResults":
        result = result.get("tweet", {})

    legacy = result.get("legacy", {})
    if not legacy:
        return None

    return {
        "id": legacy.get("id_str"),
        "text": legacy.get("full_text", ""),
        "created_at": legacy.get("created_at"),
        "user": extract_user(result),
        "metrics": {
            "likes": legacy.get("favorite_count", 0),
            "retweets": legacy.get("retweet_count", 0),
            "replies": legacy.get("reply_count", 0),
            "views": result.get("views", {}).get("count", "0"),
        },
        "is_retweet": legacy.get("retweeted_status_result") is not None,
        "is_quote": legacy.get("is_quote_status", False),
        "_query": query_label,
    }


def fetch_one_query(
    raw_query: str,
    target: int,
    auth_token: str,
    ct0: str,
    product: str = "Top",
    delay: float = 0.4,
    query_label: str | None = None,
) -> list:
    headers = get_headers(auth_token, ct0)
    ctx = ssl.create_default_context()
    endpoint = ENDPOINT_TEMPLATE.format(qid=SEARCH_QUERY_ID)
    label = query_label or raw_query

    tweets: list = []
    cursor: str | None = None

    print(f"[{label}] target={target} product={product}")

    while len(tweets) < target:
        variables = {
            "rawQuery": raw_query,
            "count": 20,
            "querySource": "typed_query",
            "product": product,
            "withGrokTranslatedBio": False,
            # SearchTimeline now declares this variable; its absence triggers
            # GRAPHQL_VALIDATION_FAILED. Keep in sync with the live bundle.
            "withQuickPromoteEligibilityTweetFields": False,
        }
        if cursor:
            variables["cursor"] = cursor

        body = json.dumps({
            "variables": variables,
            "features": FEATURES,
            "queryId": SEARCH_QUERY_ID,
        }).encode("utf-8")

        raw = ""
        try:
            req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                status = resp.status
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500]
            print(f"  HTTP {e.code}: {body}", file=sys.stderr)
            if e.code == 404:
                print(
                    f"  → SearchTimeline queryId may have rotated. "
                    f"Current: {SEARCH_QUERY_ID}. Set X_SEARCH_QUERY_ID secret to override.",
                    file=sys.stderr,
                )
            if e.code in (400, 422):
                print(
                    "  → GRAPHQL_VALIDATION_FAILED: request shape is stale vs X's current "
                    "schema (FEATURES / variables / queryId out of sync) — NOT an auth problem. "
                    "Re-capture features+variables+queryId from a live x.com SearchTimeline "
                    "request (DevTools → Network → Payload) and update FEATURES / X_SEARCH_QUERY_ID.",
                    file=sys.stderr,
                )
            if e.code in (401, 403):
                print("  → Auth tokens (X_AUTH_TOKEN / X_CT0) expired. Re-extract from browser.", file=sys.stderr)
            break
        except Exception as e:
            print(f"  Network exception: {e}", file=sys.stderr)
            break

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as je:
            print(f"  HTTP {status} but body is not JSON: {je}", file=sys.stderr)
            print(f"  qid={SEARCH_QUERY_ID}  endpoint={endpoint}", file=sys.stderr)
            print(f"  resp[:400]: {raw[:400]!r}", file=sys.stderr)
            break

        if "errors" in data and not data.get("data"):
            print(f"  GraphQL errors: {json.dumps(data['errors'])[:400]}", file=sys.stderr)
            break

        timeline = (
            data.get("data", {})
            .get("search_by_raw_query", {})
            .get("search_timeline", {})
            .get("timeline", {})
        )
        instructions = timeline.get("instructions", [])
        entries = _iter_entries(instructions)

        new_cursor = None
        batch = 0
        for entry in entries:
            entry_id = entry.get("entryId", "")
            if "cursor-bottom" in entry_id:
                new_cursor = entry.get("content", {}).get("value")
                continue
            tweet = _parse_tweet_entry(entry, label)
            if tweet:
                tweets.append(tweet)
                batch += 1

        print(f"  +{batch} (total {len(tweets)})")

        if not new_cursor or batch == 0:
            print(f"  [{label}] done (no more pages)")
            break

        cursor = new_cursor
        time.sleep(delay)

    return tweets


def _load_yaml(path: str):
    try:
        import yaml  # type: ignore
    except ImportError:
        print(f"{path} requires PyYAML (pip install pyyaml)", file=sys.stderr)
        sys.exit(2)
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_queries(args) -> list[dict]:
    """Build the query list from keyword files and/or a curated-account allowlist.

    Returns list of {label, query, count, product}.
    """
    queries: list[dict] = []

    # --- keyword discovery stream ---
    if args.queries_file:
        cfg = _load_yaml(args.queries_file)
        defaults = cfg.get("defaults", {}) or {}
        for q in cfg.get("queries", []):
            queries.append({
                "label": q.get("label", q["query"][:40]),
                "query": q["query"],
                "count": q.get("count", defaults.get("count", args.per_query_count)),
                "product": q.get("product", defaults.get("product", "Top")),
            })
    if args.query:
        queries.append({
            "label": args.query[:40],
            "query": args.query,
            "count": args.per_query_count,
            "product": args.product,
        })

    # --- curated source stream (from:<handle>) ---
    if args.accounts_file:
        acfg = _load_yaml(args.accounts_file)
        adefaults = acfg.get("defaults", {}) or {}
        acount = adefaults.get("count", 15)
        for a in acfg.get("accounts", []):
            handle = a.get("handle") if isinstance(a, dict) else a
            if not handle:
                continue
            queries.append({
                "label": f"src:{handle}",
                # originals only — that account's recent industry posts, regardless of likes
                "query": f"from:{handle} -filter:replies",
                "count": (a.get("count", acount) if isinstance(a, dict) else acount),
                "product": "Latest",
            })

    if not queries:
        print("Provide --query, --queries-file, or --accounts-file", file=sys.stderr)
        sys.exit(2)
    return queries


def parse_twitter_date(date_str: str) -> datetime | None:
    """X 'created_at' format: 'Wed Jun 04 12:34:56 +0000 2026'."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return None


def filter_by_age(tweets: list, max_age_hours: float) -> tuple[list, int]:
    """Drop tweets older than `max_age_hours`. Returns (kept, dropped_count)."""
    if max_age_hours <= 0:
        return tweets, 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    kept = []
    dropped = 0
    for t in tweets:
        dt = parse_twitter_date(t.get("created_at", ""))
        if dt is None or dt >= cutoff:
            kept.append(t)
        else:
            dropped += 1
    return kept, dropped


def dedup_tweets(tweets: list) -> list:
    seen: dict[str, dict] = {}
    for t in tweets:
        tid = t.get("id")
        if not tid:
            continue
        if tid not in seen:
            seen[tid] = t
        else:
            seen[tid]["_query"] = f"{seen[tid].get('_query','')}|{t.get('_query','')}"
    return list(seen.values())


def main():
    parser = argparse.ArgumentParser(description="Fetch tweets via X SearchTimeline")
    parser.add_argument("--query", help="Single raw query string")
    parser.add_argument("--queries-file", help="YAML file with list of keyword queries")
    parser.add_argument("--accounts-file", help="YAML allowlist of industry accounts (fetched via from:<handle>)")
    parser.add_argument("--per-query-count", type=int, default=200, help="Target tweets per query")
    parser.add_argument("--product", default="Latest", choices=["Top", "Latest"], help="Default product when --query is used")
    parser.add_argument("--max-age-hours", type=float, default=48, help="Drop tweets older than this many hours (0 = no filter)")
    parser.add_argument("--output", default="data/search.json")
    parser.add_argument("--delay", type=float, default=0.4)
    args = parser.parse_args()

    if not AUTH_TOKEN or not CT0:
        print("Set X_AUTH_TOKEN and X_CT0 environment variables", file=sys.stderr)
        sys.exit(1)

    queries = load_queries(args)
    print(f"Running {len(queries)} search query/queries (qid={SEARCH_QUERY_ID})")

    all_tweets: list = []
    for q in queries:
        ts = fetch_one_query(
            raw_query=q["query"],
            target=q["count"],
            auth_token=AUTH_TOKEN,
            ct0=CT0,
            product=q["product"],
            delay=args.delay,
            query_label=q["label"],
        )
        all_tweets.extend(ts)

    deduped = dedup_tweets(all_tweets)
    fresh, dropped = filter_by_age(deduped, args.max_age_hours)
    if args.max_age_hours > 0:
        print(f"Time filter: kept {len(fresh)} (dropped {dropped} older than {args.max_age_hours}h)")

    out_dir = os.path.dirname(args.output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(fresh, f, ensure_ascii=False, indent=2)

    print(f"\nSaved {len(fresh)} unique tweets (from {len(all_tweets)} raw) to {args.output}")
    if fresh:
        total_likes = sum(t["metrics"]["likes"] for t in fresh)
        unique_users = len({t["user"]["screen_name"] for t in fresh})
        print(f"  unique users: {unique_users}, total likes: {total_likes:,}")
        top = sorted(fresh, key=lambda t: t["metrics"]["likes"], reverse=True)[:5]
        print("  top 5 by likes:")
        for t in top:
            txt = t["text"][:60].replace("\n", " ")
            print(f"    @{t['user']['screen_name']} ({t['metrics']['likes']:,}): {txt}")


if __name__ == "__main__":
    main()
