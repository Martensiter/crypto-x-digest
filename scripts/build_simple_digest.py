#!/usr/bin/env python3
"""
Build a digest (no LLM) tuned for INDUSTRY SIGNAL over engagement-farmed noise.

Input: the output of filter_promotional.py (a JSON list of tweet dicts carrying
`user.verified`, `user.followers`, `metrics.likes`, `created_at`, and
`filter_scores.{news_score,promo_score}`). Also accepts {"tweets": [...]}.
Optionally a curated source allowlist (--accounts-file) — the same file
fetch_search.py uses.

Design:
  1. RELIABILITY ranking — combines curated-source membership, verified status,
     follower reach (log-scaled), engagement-to-follower ratio, and upstream
     news/promo scores. A "farming penalty" demotes low-reach accounts with an
     absurd like:follower ratio (classic "impression zombie"). Trusted sources
     (curated ★ / verified ✅) are surfaced first in every section.
  2. MOMENTUM aggregation (deterministic, code-only), QUALITY-WEIGHTED — per
     entity (coin / $ticker / #hashtag / proper noun) we count how many distinct
     *quality* accounts (curated, verified, or >= follower floor) are talking
     about it. This defeats zombie swarms that would otherwise inflate momentum.
     Specific topics (ETF, SEC, names...) are preferred over broad coin buckets.

Labels are kept in English — translate_digest.py renders the JA copy afterwards.
Stdlib only (PyYAML is used only to read the allowlist, and degrades gracefully).

Usage:
    python build_simple_digest.py data/filtered.json --output digest.md \
        --accounts-file config/source_accounts.yaml --title "Crypto Digest (X Search)"
"""

import json
import re
import math
import argparse
from collections import defaultdict
from datetime import datetime, timezone

# --- reliability weights (tunable) -----------------------------------------
REL_W_CURATED = 0.35    # tweet from a curated industry source (strongest trust signal)
REL_W_VERIFIED = 0.40   # verified (X Blue is paid, so combined with the verified-first tier)
REL_W_FOLLOWERS = 0.25  # scaled by log10(followers)/6  (≈1M followers → full)
REL_W_RATIO = 0.15      # likes / followers (organic resonance)
REL_W_NEWS = 0.20       # news_score from filter_promotional.py
REL_W_PROMO = 0.10      # subtracted, * promo_score
FARMING_PENALTY = 0.30  # subtracted from suspected impression-farming accounts

# --- momentum / layout knobs (overridable via CLI) -------------------------
MOMENTUM_MIN_ACCOUNTS = 2   # an entity needs >= this many QUALITY accounts to count
MOMENTUM_TOP_ENTITIES = 12  # how many stories to show
TWEETS_PER_TOPIC = 6        # sources listed per story
OTHER_CAP = 8               # "Other notable" tail
RECENCY_HOURS = 12          # window for the "recent" concentration signal
BROAD_PENALTY = 0.6         # broad coin buckets down-weighted so specific stories rank higher
QUALITY_FOLLOWERS = 5000    # follower floor that counts as a "quality" account
FARMING_FOLLOWERS = 2000    # below this (and untrusted) an absurd like-ratio = farming
FARMING_RATIO = 1.5         # likes/followers above this on a small untrusted account = suspicious

# Populated from --accounts-file (lowercased handles).
CURATED_HANDLES: set[str] = set()

# --- entity canonicalization (crypto-scoped feed) --------------------------
ENTITY_SYNONYMS = {
    "btc": "Bitcoin", "bitcoin": "Bitcoin", "$btc": "Bitcoin", "ビットコイン": "Bitcoin",
    "eth": "Ethereum", "ethereum": "Ethereum", "$eth": "Ethereum", "イーサリアム": "Ethereum",
    "sol": "Solana", "solana": "Solana", "$sol": "Solana", "ソラナ": "Solana",
    "xrp": "XRP", "$xrp": "XRP", "ripple": "XRP", "リップル": "XRP",
    "doge": "Dogecoin", "dogecoin": "Dogecoin", "$doge": "Dogecoin",
    "defi": "DeFi", "ディーファイ": "DeFi",
    "nft": "NFT",
    "web3": "Web3",
    "stablecoin": "Stablecoin", "usdc": "Stablecoin", "usdt": "Stablecoin", "ステーブルコイン": "Stablecoin",
    "etf": "ETF",
    "sec": "SEC",
    "coinbase": "Coinbase",
    "binance": "Binance",
    "microstrategy": "MicroStrategy", "saylor": "MicroStrategy",
    "blackrock": "BlackRock",
}
JA_TERMS = [t for t in ENTITY_SYNONYMS if not t.isascii()]

# Broad "asset" buckets — kept, but de-prioritized vs. specific stories.
BROAD_ENTITIES = {"Bitcoin", "Ethereum", "Solana", "XRP", "Dogecoin", "DeFi", "NFT", "Web3", "Stablecoin"}

TICKER_RE = re.compile(r"\$[A-Za-z]{2,6}\b")
HASHTAG_RE = re.compile(r"#(\w+)")
TOKEN_RE = re.compile(r"[a-z0-9]{2,}")
PROPER_RE = re.compile(r"[A-Z][A-Za-z]{2,}")

STOPWORDS = {
    "The", "This", "That", "With", "From", "Have", "Will", "Just", "Your", "About",
    "What", "When", "Where", "Here", "There", "They", "Their", "Them", "Then",
    "And", "But", "For", "Not", "You", "All", "Are", "Was", "Has", "Now", "New",
    "Why", "How", "Who", "Out", "Get", "One", "Two", "Today", "Via", "RT",
}


def entities_in(text: str) -> set[str]:
    """Canonical entities mentioned in a tweet (coins, tickers, hashtags, proper nouns)."""
    ents: set[str] = set()
    low = text.lower()
    for m in TICKER_RE.findall(text):
        ents.add(ENTITY_SYNONYMS.get(m.lower(), m.upper()))
    for h in HASHTAG_RE.findall(text):
        ents.add(ENTITY_SYNONYMS.get(h.lower(), "#" + h))
    for w in TOKEN_RE.findall(low):
        if w in ENTITY_SYNONYMS:
            ents.add(ENTITY_SYNONYMS[w])
    for t in JA_TERMS:
        if t in text:
            ents.add(ENTITY_SYNONYMS[t])
    for w in PROPER_RE.findall(text):
        if w.lower() in ENTITY_SYNONYMS:
            ents.add(ENTITY_SYNONYMS[w.lower()])
        elif w not in STOPWORDS:
            ents.add(w)
    return ents


def parse_twitter_date(date_str: str):
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return None


def is_curated(user: dict) -> bool:
    return (user.get("screen_name", "") or "").lower() in CURATED_HANDLES


def is_quality(user: dict) -> bool:
    """A 'quality' account for momentum: curated, verified, or above the follower floor."""
    return bool(user.get("verified")) or is_curated(user) or (user.get("followers", 0) or 0) >= QUALITY_FOLLOWERS


def reliability(tweet: dict) -> float:
    """Reliability score: trust (curated/verified) + reach + resonance + news, minus farming."""
    user = tweet.get("user", {})
    metrics = tweet.get("metrics", {})
    fs = tweet.get("filter_scores", {})
    followers = user.get("followers", 0) or 0
    likes = metrics.get("likes", 0) or 0
    verified = bool(user.get("verified"))
    curated = is_curated(user)

    score = 0.0
    if curated:
        score += REL_W_CURATED
    if verified:
        score += REL_W_VERIFIED
    if followers > 0:
        score += REL_W_FOLLOWERS * min(1.0, math.log10(followers + 1) / 6.0)
    if followers > 100:
        ratio = likes / followers
        if ratio > 0.05:
            score += REL_W_RATIO
        elif ratio > 0.01:
            score += REL_W_RATIO * 0.5
    score += REL_W_NEWS * float(fs.get("news_score", 0.0))
    score -= REL_W_PROMO * float(fs.get("promo_score", 0.0))

    # impression-farming penalty: small, untrusted account with an absurd like:follower ratio
    if not verified and not curated and 0 < followers < FARMING_FOLLOWERS:
        if likes / max(followers, 1) > FARMING_RATIO:
            score -= FARMING_PENALTY

    return max(0.0, score)


def rank_key(tweet: dict):
    """Trusted-first hard tier (curated or verified), then reliability."""
    user = tweet.get("user", {})
    trusted = 1 if (is_curated(user) or user.get("verified")) else 0
    return (trusted, reliability(tweet))


def fmt_followers(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def aggregate_momentum(tweets: list, recency_hours: int) -> dict:
    """entity -> {tweets, quality(set), verified(set), curated(set), likes, recent}."""
    now = datetime.now(timezone.utc)
    agg: dict[str, dict] = defaultdict(lambda: {
        "tweets": 0, "quality": set(), "verified": set(), "curated": set(), "likes": 0, "recent": 0,
    })
    for t in tweets:
        user = t.get("user", {})
        sn = user.get("screen_name", "")
        likes = t.get("metrics", {}).get("likes", 0) or 0
        dt = parse_twitter_date(t.get("created_at", ""))
        is_recent = dt is not None and (now - dt).total_seconds() <= recency_hours * 3600
        quality = is_quality(user)
        for ent in entities_in(t.get("text", "")):
            a = agg[ent]
            a["tweets"] += 1
            if quality:
                a["quality"].add(sn)
            if user.get("verified"):
                a["verified"].add(sn)
            if is_curated(user):
                a["curated"].add(sn)
            a["likes"] += likes
            if is_recent:
                a["recent"] += 1
    return agg


def momentum_score(ent: str, stat: dict) -> float:
    """Breadth of QUALITY accounts dominates (zombie swarms don't count), + verified + recency."""
    base = len(stat["quality"]) + 0.5 * len(stat["verified"]) + 0.5 * stat["recent"]
    return base * (BROAD_PENALTY if ent in BROAD_ENTITIES else 1.0)


def render_tweet_line(t: dict) -> list[str]:
    user = t.get("user", {})
    sn = user.get("screen_name", "unknown")
    marks = ("★ " if is_curated(user) else "") + ("✅ " if user.get("verified") else "")
    followers = fmt_followers(user.get("followers", 0) or 0)
    likes = t.get("metrics", {}).get("likes", 0) or 0
    text = t.get("text", "").replace("\n", " ")
    if len(text) > 220:
        text = text[:220] + "..."
    url = f"https://x.com/{sn}/status/{t.get('id', '')}"
    return [
        f"- {marks}**@{sn}** ({likes:,} likes · {followers} followers): {text}",
        f"  [source]({url})",
        "",
    ]


def build_digest_md(tweets: list, title: str, *, min_accounts: int,
                    top_entities: int, per_topic: int, other_cap: int,
                    recency_hours: int) -> str:
    tweets = [t for t in tweets if not t.get("is_retweet")]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    n_accounts = len({t.get("user", {}).get("screen_name", "") for t in tweets})
    n_curated = len({t.get("user", {}).get("screen_name", "") for t in tweets if is_curated(t.get("user", {}))})

    lines = [
        f"# {title}",
        f"**{now} UTC** — {len(tweets)} tweets / {n_accounts} accounts "
        f"({n_curated} curated sources) / last 48h",
        "",
    ]
    if not tweets:
        lines.append("*No tweets to report.*")
        return "\n".join(lines)

    # --- momentum aggregation (quality-weighted) ---
    agg = aggregate_momentum(tweets, recency_hours)
    ranked = sorted(
        ((ent, s) for ent, s in agg.items() if len(s["quality"]) >= min_accounts),
        key=lambda kv: momentum_score(kv[0], kv[1]),
        reverse=True,
    )
    top = ranked[:top_entities]

    lines += [
        "---", "",
        "## 🔥 Momentum (last 48h)",
        "*Accounts = distinct quality accounts (curated / verified / 5k+ followers).*",
        "",
    ]
    if top:
        lines += [
            "| Topic | Tweets | Accounts | Verified | Total likes |",
            "|---|---|---|---|---|",
        ]
        for ent, s in top:
            lines.append(
                f"| {ent} | {s['tweets']} | {len(s['quality'])} | "
                f"{len(s['verified'])} | {s['likes']:,} |"
            )
        lines.append("")
    else:
        lines += ["*No topic reached the momentum threshold.*", ""]

    # --- assign each tweet to its strongest momentum entity (specific > broad) ---
    top_set = {ent for ent, _ in top}
    by_entity: dict[str, list] = defaultdict(list)
    others: list = []
    for t in tweets:
        ents = entities_in(t.get("text", "")) & top_set
        if ents:
            specifics = [e for e in ents if e not in BROAD_ENTITIES]
            pool = specifics if specifics else list(ents)
            best = max(pool, key=lambda e: momentum_score(e, agg[e]))
            by_entity[best].append(t)
        else:
            others.append(t)

    # --- stories: momentum order; sources trusted-first then reliability ---
    lines += ["---", "", "## 📰 Stories (by momentum; sources trusted-first)", ""]
    for ent, s in top:
        bucket = sorted(by_entity.get(ent, []), key=rank_key, reverse=True)
        if not bucket:
            continue
        lines.append(
            f"### {ent} — {s['tweets']} tweets / {len(s['quality'])} accounts / "
            f"{len(s['verified'])} verified"
        )
        lines.append("")
        for t in bucket[:per_topic]:
            lines += render_tweet_line(t)
        lines.append("")

    # --- tail: notable tweets not tied to a top story (trusted-first) ---
    others.sort(key=rank_key, reverse=True)
    if others:
        lines += ["---", "", "## Other notable (trusted-first)", ""]
        for t in others[:other_cap]:
            lines += render_tweet_line(t)

    return "\n".join(lines)


def load_curated_handles(path: str) -> set[str]:
    if not path:
        return set()
    try:
        import yaml  # type: ignore
    except ImportError:
        print("--accounts-file needs PyYAML; skipping curated boost", flush=True)
        return set()
    try:
        with open(path) as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return set()
    handles = set()
    for a in cfg.get("accounts", []):
        h = a.get("handle") if isinstance(a, dict) else a
        if h:
            handles.add(str(h).lower())
    return handles


def main():
    global CURATED_HANDLES
    parser = argparse.ArgumentParser(description="Build reliability-ranked digest with momentum table")
    parser.add_argument("input", help="filtered.json (filter_promotional output)")
    parser.add_argument("--output", "-o", default="digest.md", help="Output markdown file")
    parser.add_argument("--title", default="Crypto Digest (X Search)", help="Digest title")
    parser.add_argument("--accounts-file", default=None, help="Curated source allowlist YAML (for trust boost)")
    parser.add_argument("--topics", nargs="+", default=None, help="(accepted for backward-compat; ignored)")
    parser.add_argument("--min-accounts", type=int, default=MOMENTUM_MIN_ACCOUNTS)
    parser.add_argument("--top-entities", type=int, default=MOMENTUM_TOP_ENTITIES)
    parser.add_argument("--tweets-per-topic", type=int, default=TWEETS_PER_TOPIC)
    parser.add_argument("--other-cap", type=int, default=OTHER_CAP)
    parser.add_argument("--recency-hours", type=int, default=RECENCY_HOURS)
    args = parser.parse_args()

    CURATED_HANDLES = load_curated_handles(args.accounts_file)

    with open(args.input) as f:
        data = json.load(f)
    tweets = data.get("tweets", data) if isinstance(data, dict) else data
    if not isinstance(tweets, list):
        print("Error: Expected list of tweets")
        return 1

    md = build_digest_md(
        tweets, args.title,
        min_accounts=args.min_accounts,
        top_entities=args.top_entities,
        per_topic=args.tweets_per_topic,
        other_cap=args.other_cap,
        recency_hours=args.recency_hours,
    )
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(md)

    n = len([t for t in tweets if not t.get("is_retweet")])
    print(f"Saved digest to {args.output} ({n} tweets, {len(CURATED_HANDLES)} curated handles)")
    return 0


if __name__ == "__main__":
    exit(main())
