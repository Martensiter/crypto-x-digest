#!/usr/bin/env python3
"""
Filter promotional / corporate self-promotion tweets and score news relevance.

Two-stage filter:
  1. Promotional detection — flags tweets that are ads, CTAs, or corporate promos
  2. News relevance scoring — boosts tweets that are actual news / breaking information

Signals for promotional content:
  - CTA patterns ("Sign up", "Use code", "Link in bio", "Shop now", etc.)
  - Self-promotional corporate accounts (low follower engagement ratio + promo language)
  - Giveaway / contest language
  - Affiliate / referral links
  - Thread-bait ("A thread 🧵", "1/")

Signals for news relevance:
  - Corroboration: multiple accounts discussing the same topic/entity
  - Timeliness keywords ("breaking", "just announced", "confirmed")
  - High engagement relative to account size
  - Source diversity (verified journalists, official accounts)
  - Factual language over opinion ("announced", "released", "reported")

Usage:
    python scripts/filter_promotional.py data/tweets.json --output data/tweets_filtered.json
    python scripts/filter_promotional.py data/tweets.json --output data/tweets_filtered.json --strict
"""

import json
import re
import argparse
import math
from collections import Counter, defaultdict


# ============================================================================
# PROMOTIONAL DETECTION
# ============================================================================

# CTA (Call-to-Action) patterns — strong promo signal
CTA_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bsign up\b", r"\bsignup\b", r"\bregister now\b",
        r"\buse code\b", r"\bdiscount code\b", r"\bpromo code\b",
        r"\bshop now\b", r"\bbuy now\b", r"\border now\b", r"\bget yours\b",
        r"\blink in bio\b", r"\bcheck link\b", r"\bclick (?:the )?link\b",
        r"\blimited time\b", r"\bflash sale\b", r"\b\d+% off\b",
        r"\bfree trial\b", r"\bfree shipping\b",
        r"\bsubscribe (?:now|today|here)\b",
        r"\bjoin (?:us|now|today|our)\b",
        r"\bdon'?t miss (?:out|this)\b",
        r"\bgrab (?:yours|it|this)\b",
        r"\bclaim (?:your|now)\b",
        r"\bunlock\b.*\bfree\b",
        r"\bgiveaway\b", r"\b(?:enter to )?win\b.*\b(?:prize|giveaway)\b",
        r"\bretweet (?:to|and|&) (?:win|enter)\b",
        r"\bfollow (?:us|me) (?:and|&|to)\b",
        r"\bwe(?:'re| are) (?:hiring|looking for)\b",
        r"\bapply (?:now|today|here)\b",
        r"\blearn more at\b",
        r"\bvisit (?:us at|our)\b",
    ]
]

# Thread-bait patterns (lower signal, context-dependent)
THREAD_BAIT_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"^(?:1[/.)]\s|🧵)",
        r"\ba thread\b.*🧵",
        r"\bhere(?:'s| is) (?:a |my |the )?thread\b",
        r"\blet me (?:explain|break|walk)\b",
        r"\b(?:mega|master) ?thread\b",
    ]
]

# Affiliate / tracking URL patterns
AFFILIATE_URL_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"(?:ref|affiliate|partner|utm_source|utm_campaign)=",
        r"\b(?:amzn\.to|bit\.ly|tinyurl|shorte\.st|linktr\.ee)\b",
    ]
]

# Known promotional account patterns (screen_name substrings)
PROMO_ACCOUNT_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"deals$", r"offers$", r"promo", r"official$",
        r"^get", r"shop", r"^try",
    ]
]


def compute_promo_score(tweet: dict) -> float:
    """
    Compute a promotional score (0.0 = organic, 1.0 = definitely promo).

    Returns a float between 0 and 1.
    """
    text = tweet.get("text", "")
    user = tweet.get("user", {})
    screen_name = user.get("screen_name", "").lower()
    score = 0.0

    # CTA patterns (strong signal: +0.35 each, max 0.7)
    cta_hits = sum(1 for p in CTA_PATTERNS if p.search(text))
    score += min(cta_hits * 0.35, 0.7)

    # Thread-bait (mild signal: +0.1)
    if any(p.search(text) for p in THREAD_BAIT_PATTERNS):
        score += 0.1

    # Affiliate URLs (+0.3)
    if any(p.search(text) for p in AFFILIATE_URL_PATTERNS):
        score += 0.3

    # Promo account name patterns (+0.2)
    if any(p.search(screen_name) for p in PROMO_ACCOUNT_PATTERNS):
        score += 0.2

    # Excessive hashtags (>4) — often promotional (+0.15)
    hashtag_count = len(re.findall(r"#\w+", text))
    if hashtag_count > 4:
        score += 0.15

    # Excessive emojis (>6) in promotional context (+0.1)
    emoji_pattern = re.compile(
        "[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U0001F900-\U0001F9FF"
        "\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF]+", re.UNICODE
    )
    emoji_count = len(emoji_pattern.findall(text))
    if emoji_count > 6:
        score += 0.1

    # Very short tweet with just a link — often promo
    text_no_urls = re.sub(r"https?://\S+", "", text).strip()
    if len(text_no_urls) < 30 and "http" in text:
        score += 0.1

    return min(score, 1.0)


# ============================================================================
# NEWS RELEVANCE SCORING
# ============================================================================

# Timeliness / breaking news keywords
NEWS_KEYWORDS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bbreaking\b", r"\bjust (?:announced|confirmed|released|reported|happened)\b",
        r"\bexclusive\b", r"\bconfirmed\b",
        r"\bnew(?:ly)? (?:announced|released|launched|discovered|revealed|published)\b",
        r"\bofficially?\b", r"\breport(?:s|ed|ing)?\b",
        r"\baccording to\b", r"\bsources? say\b", r"\b(?:per|via) @\w+\b",
        r"\b(?:announced|reveals?|unveils?|launches?|releases?)\b",
        r"\bupdate(?:d|s)?\b.*\b(?:on|to|about)\b",
        r"\b(?:study|research) (?:shows?|finds?|reveals?|confirms?)\b",
    ]
]

# Factual language (vs opinion/reaction)
FACTUAL_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\b(?:said|stated|announced|confirmed|denied)\b",
        r"\b(?:according|reportedly|sources)\b",
        r"\b(?:data|numbers?|statistics?|figures?|percent|%)\b",
        r"\b(?:million|billion|trillion)\b",
        r"\b(?:CEO|CTO|CFO|president|prime minister|spokesperson)\b",
    ]
]


def compute_news_score(tweet: dict, corroboration_map: dict = None) -> float:
    """
    Compute news relevance score (0.0 = irrelevant, 1.0 = highly newsworthy).

    Signals:
    - Timeliness keywords (+0.2)
    - Factual language (+0.15)
    - Corroboration: entities mentioned by multiple users (+0.3)
    - High engagement-to-follower ratio (+0.15)
    - Not a reply (original content more likely news) (+0.1)
    - Verified / high-follower account (+0.1)
    """
    text = tweet.get("text", "")
    user = tweet.get("user", {})
    metrics = tweet.get("metrics", {})
    score = 0.0

    # Timeliness keywords
    news_hits = sum(1 for p in NEWS_KEYWORDS if p.search(text))
    score += min(news_hits * 0.1, 0.2)

    # Factual language
    fact_hits = sum(1 for p in FACTUAL_PATTERNS if p.search(text))
    score += min(fact_hits * 0.05, 0.15)

    # Corroboration: shared entities discussed by multiple distinct users
    if corroboration_map:
        entities = extract_news_entities(text)
        max_corroboration = 0
        for entity in entities:
            key = entity.lower()
            if key in corroboration_map:
                max_corroboration = max(max_corroboration, corroboration_map[key])
        # Normalize: 1 user = 0, 5+ users = 0.3
        if max_corroboration > 1:
            score += min((max_corroboration - 1) * 0.075, 0.3)

    # Engagement-to-follower ratio
    likes = metrics.get("likes", 0)
    followers = user.get("followers", 0)
    if followers > 100:
        ratio = likes / followers
        if ratio > 0.1:   # 10%+ engagement = exceptional
            score += 0.15
        elif ratio > 0.01:  # 1%+ = good
            score += 0.08

    # Not a reply — original tweets more likely to carry news
    if not text.startswith("@"):
        score += 0.1

    # Verified or high-follower accounts
    if user.get("verified") or followers >= 50000:
        score += 0.1

    return min(score, 1.0)


def extract_news_entities(text: str) -> list:
    """Extract potential news entities for corroboration check."""
    # Proper nouns (capitalized words not at sentence start)
    words = text.split()
    entities = []

    # Named entities (capitalized 2+ letter words)
    for word in words:
        clean = re.sub(r"[^\w]", "", word)
        if clean and clean[0].isupper() and len(clean) > 2:
            # Skip very common words
            if clean.lower() not in {
                "the", "this", "that", "what", "when", "where",
                "why", "how", "just", "now", "new", "today", "here",
                "not", "but", "and", "for", "all", "its", "has",
                "was", "are", "been", "have", "will", "can",
            }:
                entities.append(clean)

    # Also extract hashtags as entities
    hashtags = re.findall(r"#(\w+)", text)
    entities.extend(hashtags)

    return entities


def build_corroboration_map(tweets: list) -> dict:
    """
    Build entity -> unique user count map for corroboration scoring.

    An entity mentioned by many different users is likely news (vs. personal opinion).
    """
    entity_users = defaultdict(set)

    for tweet in tweets:
        text = tweet.get("text", "")
        user = tweet.get("user", {}).get("screen_name", "")
        entities = extract_news_entities(text)

        for entity in entities:
            entity_users[entity.lower()].add(user)

    return {entity: len(users) for entity, users in entity_users.items()}


# ============================================================================
# MAIN FILTER
# ============================================================================

def filter_and_score(tweets: list, promo_threshold: float = 0.4,
                     min_news_score: float = 0.0, strict: bool = False) -> list:
    """
    Filter promotional content and score news relevance.

    Args:
        tweets: list of tweet dicts
        promo_threshold: tweets with promo_score >= this are removed
        min_news_score: minimum news_score to keep (0.0 = keep all non-promo)
        strict: if True, use tighter thresholds (promo=0.3, news=0.15)

    Returns:
        Filtered and scored tweet list, sorted by news_score descending.
    """
    if strict:
        promo_threshold = 0.3
        min_news_score = 0.15

    # Build corroboration map across all tweets
    corroboration_map = build_corroboration_map(tweets)

    filtered = []
    promo_removed = 0
    news_removed = 0

    for tweet in tweets:
        promo_score = compute_promo_score(tweet)
        news_score = compute_news_score(tweet, corroboration_map)

        tweet["filter_scores"] = {
            "promo_score": round(promo_score, 3),
            "news_score": round(news_score, 3),
        }

        if promo_score >= promo_threshold:
            promo_removed += 1
            continue

        if news_score < min_news_score:
            news_removed += 1
            continue

        filtered.append(tweet)

    print(f"Filter results: {len(tweets)} -> {len(filtered)} tweets")
    print(f"  Promotional removed: {promo_removed}")
    print(f"  Low news relevance removed: {news_removed}")

    # Sort by composite score: news_score high, promo_score low
    filtered.sort(
        key=lambda t: (
            t["filter_scores"]["news_score"]
            - t["filter_scores"]["promo_score"] * 0.5
        ),
        reverse=True,
    )

    return filtered


def main():
    parser = argparse.ArgumentParser(
        description="Filter promotional tweets and score news relevance"
    )
    parser.add_argument("input", help="Input JSON file with tweets")
    parser.add_argument("--output", "-o", required=True, help="Output JSON file")
    parser.add_argument(
        "--promo-threshold", type=float, default=0.4,
        help="Promo score threshold for removal (default: 0.4)"
    )
    parser.add_argument(
        "--min-news-score", type=float, default=0.0,
        help="Minimum news score to keep (default: 0.0 = keep all non-promo)"
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Strict mode: promo=0.3, min_news=0.15"
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Print detailed statistics"
    )
    args = parser.parse_args()

    with open(args.input) as f:
        data = json.load(f)

    # Handle both raw list and enriched format
    if isinstance(data, dict) and "tweets" in data:
        tweets = data["tweets"]
    elif isinstance(data, list):
        tweets = data
    else:
        print("Error: Expected list of tweets or {tweets: [...]}")
        return 1

    print(f"Loaded {len(tweets)} tweets")

    # Remove retweets first
    original = [t for t in tweets if not t.get("is_retweet")]
    print(f"Original tweets (no RTs): {len(original)}")

    filtered = filter_and_score(
        original,
        promo_threshold=args.promo_threshold,
        min_news_score=args.min_news_score,
        strict=args.strict,
    )

    if args.stats and filtered:
        print("\n=== Score Distribution ===")
        promo_scores = [t["filter_scores"]["promo_score"] for t in filtered]
        news_scores = [t["filter_scores"]["news_score"] for t in filtered]
        print(f"  Promo scores: min={min(promo_scores):.2f}, max={max(promo_scores):.2f}, "
              f"mean={sum(promo_scores)/len(promo_scores):.2f}")
        print(f"  News scores:  min={min(news_scores):.2f}, max={max(news_scores):.2f}, "
              f"mean={sum(news_scores)/len(news_scores):.2f}")

        # Top 10 most newsworthy
        print("\n=== Top 10 Most Newsworthy ===")
        for t in filtered[:10]:
            user = t.get("user", {}).get("screen_name", "?")
            text = t.get("text", "")[:80].replace("\n", " ")
            ns = t["filter_scores"]["news_score"]
            ps = t["filter_scores"]["promo_score"]
            print(f"  [{ns:.2f}N / {ps:.2f}P] @{user}: {text}")

    with open(args.output, "w") as f:
        json.dump(filtered, f, indent=2)

    print(f"\nSaved {len(filtered)} filtered tweets to {args.output}")
    return 0


if __name__ == "__main__":
    exit(main())
