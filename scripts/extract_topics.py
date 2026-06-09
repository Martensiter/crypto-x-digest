#!/usr/bin/env python3
"""
Stage 1: Extract structured metadata from tweets without LLM.

Extracts:
- Topics (from hashtags, keywords, @mentions)
- Engagement tier (viral, high, medium, low)
- Content type (retweet, quote, reply, original)
- Entities (people, companies, products mentioned)

Usage:
    python extract_topics.py data/tweets.json --output data/enriched.json
"""

import json
import re
import argparse
from collections import Counter

# Topic keyword patterns (lowercase)
TOPIC_PATTERNS = {
    "ai": [
        r"\bai\b", r"\bgpt", r"\bclaude\b", r"\bllm", r"\bopenai\b", r"\banthropic\b",
        r"\bgemini\b", r"\bcopilot\b", r"\bchatgpt\b", r"\bmodel\b", r"\bagent",
        r"\bmachine learning\b", r"\bdeep learning\b", r"\bneural\b"
    ],
    "crypto": [
        r"\bbtc\b", r"\beth\b", r"\bbitcoin\b", r"\bethereum\b", r"\bcrypto\b",
        r"\bsolana\b", r"\bnft\b", r"\bdefi\b", r"\bweb3\b", r"\btoken\b",
        r"\bwallet\b", r"\bblockchain\b"
    ],
    "politics": [
        r"\btrump\b", r"\bbiden\b", r"\bcongress\b", r"\bsenate\b", r"\bhouse\b",
        r"\bdemocrat", r"\brepublican", r"\bgop\b", r"\belection\b", r"\bvote\b",
        r"\bpolicy\b", r"\bgovernment\b", r"\bpolitics\b", r"\bpresident\b"
    ],
    "tech": [
        r"\bstartup\b", r"\bvc\b", r"\bfunding\b", r"\bipo\b", r"\bacquisition\b",
        r"\bsaas\b", r"\bapi\b", r"\bcloud\b", r"\baws\b", r"\bgoogle\b",
        r"\bmicrosoft\b", r"\bmeta\b", r"\bapple\b", r"\btesla\b"
    ],
    "finance": [
        r"\bstock\b", r"\bmarket\b", r"\btreasury\b", r"\byield\b", r"\bfed\b",
        r"\binflation\b", r"\brecession\b", r"\bearnings\b", r"\bipo\b",
        r"\bs&p\b", r"\bnasdaq\b", r"\bdow\b"
    ],
    "culture": [
        r"\bmeme\b", r"\bviral\b", r"\btrending\b", r"\bmovie\b", r"\bmusic\b",
        r"\bgame\b", r"\bsports\b", r"\bnfl\b", r"\bnba\b", r"\bmlb\b"
    ],
    "science": [
        r"\bspace\b", r"\bnasa\b", r"\bclimate\b", r"\bresearch\b", r"\bstudy\b",
        r"\bscientist\b", r"\bdiscovery\b", r"\bmoon\b", r"\bmars\b"
    ],
    "geopolitics": [
        r"\bukraine\b", r"\brussia\b", r"\bchina\b", r"\bisrael\b", r"\bgaza\b",
        r"\bpalestine\b", r"\bnato\b", r"\beu\b", r"\bwar\b", r"\bmilitary\b",
        r"\bgreenland\b", r"\bcanada\b", r"\bdavos\b"
    ]
}

# Compile patterns
COMPILED_PATTERNS = {
    topic: [re.compile(p, re.IGNORECASE) for p in patterns]
    for topic, patterns in TOPIC_PATTERNS.items()
}

# Known entities (accounts that signal topics)
ACCOUNT_TOPICS = {
    # AI
    "sama": "ai", "daboromell": "ai", "klogg": "ai", "emollick": "ai",
    "ylecun": "ai", "ilyasut": "ai", "janleike": "ai", "aaborowell": "ai",
    # Crypto
    "vitalikbuterin": "crypto", "saylor": "crypto", "caborinbase": "crypto",
    "brian_armstrong": "crypto", "cz_binance": "crypto",
    # Politics
    "potus": "politics", "whitehouse": "politics", "speaker": "politics",
    # Tech
    "elonmusk": "tech", "sataboranadella": "tech", "timcook": "tech",
    # Finance
    "unusual_whales": "finance", "zerohedge": "finance"
}


def extract_hashtags(text: str) -> list:
    """Extract hashtags from tweet text."""
    return re.findall(r"#(\w+)", text.lower())


def extract_mentions(text: str) -> list:
    """Extract @mentions from tweet text."""
    return re.findall(r"@(\w+)", text.lower())


def extract_urls(text: str) -> list:
    """Extract URLs from tweet text."""
    return re.findall(r"https?://\S+", text)


def classify_topics(text: str, mentions: list, hashtags: list) -> list:
    """Classify tweet into topic categories."""
    topics = set()
    text_lower = text.lower()

    # Check text patterns
    for topic, patterns in COMPILED_PATTERNS.items():
        for pattern in patterns:
            if pattern.search(text_lower):
                topics.add(topic)
                break

    # Check account associations
    for mention in mentions:
        if mention in ACCOUNT_TOPICS:
            topics.add(ACCOUNT_TOPICS[mention])

    # Check hashtags
    for tag in hashtags:
        for topic, patterns in COMPILED_PATTERNS.items():
            for pattern in patterns:
                if pattern.search(tag):
                    topics.add(topic)
                    break

    return list(topics) if topics else ["general"]


def engagement_tier(likes: int, views: int) -> str:
    """Classify engagement level."""
    if likes >= 50000:
        return "viral"
    elif likes >= 10000:
        return "high"
    elif likes >= 1000:
        return "medium"
    else:
        return "low"


def content_type(tweet: dict) -> str:
    """Determine content type."""
    if tweet.get("is_retweet"):
        return "retweet"
    elif tweet.get("is_quote"):
        return "quote"
    elif tweet["text"].startswith("@"):
        return "reply"
    else:
        return "original"


def extract_entities(text: str) -> dict:
    """Extract named entities (simple pattern matching)."""
    entities = {
        "people": [],
        "companies": [],
        "products": []
    }

    # Common company names
    companies = [
        "OpenAI", "Anthropic", "Google", "Microsoft", "Meta", "Apple", "Tesla",
        "Amazon", "Netflix", "Nvidia", "AMD", "Intel", "Coinbase", "Stripe",
        "Vercel", "Figma", "Notion", "Slack", "Discord", "Twitter", "X"
    ]
    for company in companies:
        if re.search(rf"\b{company}\b", text, re.IGNORECASE):
            entities["companies"].append(company)

    # Common products/models
    products = [
        "GPT-4", "GPT-5", "Claude", "Gemini", "Copilot", "ChatGPT",
        "iPhone", "Vision Pro", "Model S", "Cybertruck"
    ]
    for product in products:
        if re.search(rf"\b{re.escape(product)}\b", text, re.IGNORECASE):
            entities["products"].append(product)

    return entities


def enrich_tweet(tweet: dict, force_topics: list[str] | None = None) -> dict:
    """Add extracted metadata to tweet.

    If `force_topics` is provided, those topics are merged into the result
    (and `general` is dropped). Used when the input is already topic-scoped
    (e.g. a crypto-only search) and the keyword classifier would otherwise
    misclassify Japanese tweets as `general`.
    """
    text = tweet.get("text", "")
    likes = tweet.get("metrics", {}).get("likes", 0)
    views_str = tweet.get("metrics", {}).get("views", "0")
    views = int(views_str) if views_str and views_str.isdigit() else 0

    hashtags = extract_hashtags(text)
    mentions = extract_mentions(text)
    urls = extract_urls(text)
    topics = classify_topics(text, mentions, hashtags)
    if force_topics:
        topics = [t for t in topics if t != "general"]
        for ft in force_topics:
            if ft not in topics:
                topics.insert(0, ft)
    entities = extract_entities(text)

    return {
        **tweet,
        "extracted": {
            "hashtags": hashtags,
            "mentions": mentions,
            "urls": urls,
            "topics": topics,
            "entities": entities,
            "engagement_tier": engagement_tier(likes, views),
            "content_type": content_type(tweet)
        }
    }


def analyze_corpus(tweets: list) -> dict:
    """Generate corpus-level statistics."""
    topic_counts = Counter()
    tier_counts = Counter()
    type_counts = Counter()

    for tweet in tweets:
        ext = tweet.get("extracted", {})
        for topic in ext.get("topics", []):
            topic_counts[topic] += 1
        tier_counts[ext.get("engagement_tier", "unknown")] += 1
        type_counts[ext.get("content_type", "unknown")] += 1

    return {
        "total_tweets": len(tweets),
        "topic_distribution": dict(topic_counts.most_common()),
        "engagement_distribution": dict(tier_counts),
        "content_type_distribution": dict(type_counts)
    }


def deduplicate_tweets(tweets: list) -> list:
    """Remove duplicate tweets by ID."""
    seen = set()
    unique = []
    for tweet in tweets:
        tweet_id = tweet.get("id")
        if tweet_id and tweet_id not in seen:
            seen.add(tweet_id)
            unique.append(tweet)
    return unique


def main():
    parser = argparse.ArgumentParser(description="Extract topics from tweets")
    parser.add_argument("input", help="Input JSON file with tweets")
    parser.add_argument("--output", "-o", help="Output JSON file", default=None)
    parser.add_argument(
        "--force-topic",
        action="append",
        default=None,
        help="Force this topic onto every tweet (repeatable). Use when input is already topic-scoped (e.g. a crypto-only search).",
    )
    args = parser.parse_args()

    # Load tweets
    with open(args.input) as f:
        tweets = json.load(f)

    print(f"Loaded {len(tweets)} tweets")

    # Deduplicate
    tweets = deduplicate_tweets(tweets)
    print(f"After deduplication: {len(tweets)} unique tweets")

    # Filter retweets for analysis (keep in output)
    original_tweets = [t for t in tweets if not t.get("is_retweet")]
    print(f"  {len(original_tweets)} original tweets (excluding retweets)")

    # Enrich all tweets
    enriched = [enrich_tweet(t, force_topics=args.force_topic) for t in tweets]

    # Analyze
    stats = analyze_corpus([t for t in enriched if not t.get("is_retweet")])

    print(f"\nTopic distribution:")
    for topic, count in sorted(stats["topic_distribution"].items(), key=lambda x: -x[1]):
        pct = 100 * count / stats["total_tweets"]
        print(f"  {topic}: {count} ({pct:.1f}%)")

    print(f"\nEngagement tiers:")
    for tier, count in stats["engagement_distribution"].items():
        pct = 100 * count / stats["total_tweets"]
        print(f"  {tier}: {count} ({pct:.1f}%)")

    # Output
    output = {
        "tweets": enriched,
        "stats": stats
    }

    if args.output:
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\nSaved to {args.output}")
    else:
        # Print sample
        print("\nSample enriched tweet:")
        sample = next((t for t in enriched if t["extracted"]["topics"] != ["general"]), enriched[0])
        print(json.dumps(sample["extracted"], indent=2))


if __name__ == "__main__":
    main()
