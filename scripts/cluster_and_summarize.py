#!/usr/bin/env python3
"""
Stage 2 & 3: Cluster tweets by topic and prepare for LLM summarization.

Groups tweets by:
1. Detected topic (ai, crypto, politics, etc.)
2. Engagement tier (prioritize viral/high)
3. Time window (for trending detection)

Outputs clusters ready for parallel LLM summarization.

Usage:
    python cluster_and_summarize.py data/enriched.json --output data/clusters/
"""

import json
import argparse
import os
from collections import defaultdict
from datetime import datetime


def parse_twitter_date(date_str: str) -> datetime:
    """Parse Twitter's date format."""
    try:
        return datetime.strptime(date_str, "%a %b %d %H:%M:%S %z %Y")
    except:
        return datetime.now()


def cluster_by_topic(tweets: list) -> dict:
    """Group tweets by primary topic."""
    clusters = defaultdict(list)

    for tweet in tweets:
        # Skip retweets
        if tweet.get("is_retweet"):
            continue

        ext = tweet.get("extracted", {})
        topics = ext.get("topics", ["general"])

        # Assign to first topic (could assign to multiple)
        primary_topic = topics[0]
        clusters[primary_topic].append(tweet)

    return dict(clusters)


def prioritize_tweets(tweets: list, max_per_cluster: int = 100) -> list:
    """
    Prioritize tweets for summarization.

    Strategy:
    1. Always include viral tweets
    2. Include high engagement tweets
    3. Fill remaining with diverse medium/low tweets
    """
    tiers = {
        "viral": [],
        "high": [],
        "medium": [],
        "low": []
    }

    for tweet in tweets:
        tier = tweet.get("extracted", {}).get("engagement_tier", "low")
        tiers[tier].append(tweet)

    # Sort each tier by likes
    for tier in tiers:
        tiers[tier].sort(key=lambda t: t["metrics"]["likes"], reverse=True)

    # Build prioritized list
    result = []

    # All viral
    result.extend(tiers["viral"])

    # All high
    result.extend(tiers["high"])

    # Top medium
    remaining = max_per_cluster - len(result)
    if remaining > 0:
        result.extend(tiers["medium"][:remaining])

    # If still space, add some low (for diversity)
    remaining = max_per_cluster - len(result)
    if remaining > 0:
        result.extend(tiers["low"][:remaining])

    return result[:max_per_cluster]


def format_cluster_for_llm(topic: str, tweets: list) -> str:
    """Format cluster as prompt for LLM summarization."""
    lines = [
        f"# Topic: {topic.upper()}",
        f"# Tweet count: {len(tweets)}",
        "",
        "Summarize the key narratives, stories, and sentiment in these tweets.",
        "Include specific examples with @handles and tweet IDs for sourcing.",
        "Focus on: What's happening? Why does it matter? What's the sentiment?",
        "",
        "---",
        ""
    ]

    for i, tweet in enumerate(tweets, 1):
        user = tweet.get("user", {})
        metrics = tweet.get("metrics", {})
        text = tweet.get("text", "").replace("\n", " ")

        lines.append(f"[{i}] @{user.get('screen_name', 'unknown')} ({metrics.get('likes', 0):,} likes)")
        lines.append(f"ID: {tweet.get('id', 'unknown')}")
        lines.append(f"Text: {text[:500]}")
        lines.append("")

    return "\n".join(lines)


def generate_meta_prompt(stats: dict, cluster_summaries: list) -> str:
    """Generate prompt for final synthesis."""
    return f"""You are synthesizing a Twitter timeline digest.

CORPUS STATS:
- Total tweets: {stats.get('total_tweets', 0)}
- Date range: Last 24-48 hours
- Topic distribution: {json.dumps(stats.get('topic_distribution', {}), indent=2)}

CLUSTER SUMMARIES:
{chr(10).join(cluster_summaries)}

---

Create a cohesive digest that:
1. Opens with an Executive Summary (3-4 bullets on the dominant themes)
2. Has sections for each major topic with narrative flow
3. Cites specific tweets with links: https://x.com/USER/status/ID
4. Notes overall sentiment and vibe
5. Highlights the most viral/important tweets

Format as clean Markdown suitable for reading.
"""


def main():
    parser = argparse.ArgumentParser(description="Cluster tweets for summarization")
    parser.add_argument("input", help="Input enriched JSON file")
    parser.add_argument("--output", "-o", help="Output directory for clusters", default="data/clusters")
    parser.add_argument("--max-per-cluster", type=int, default=100, help="Max tweets per cluster")
    args = parser.parse_args()

    # Load enriched tweets
    with open(args.input) as f:
        data = json.load(f)

    tweets = data.get("tweets", data) if isinstance(data, dict) else data
    stats = data.get("stats", {}) if isinstance(data, dict) else {}

    print(f"Loaded {len(tweets)} tweets")

    # Cluster by topic
    clusters = cluster_by_topic(tweets)

    print(f"\nClusters formed:")
    for topic, cluster_tweets in sorted(clusters.items(), key=lambda x: -len(x[1])):
        print(f"  {topic}: {len(cluster_tweets)} tweets")

    # Create output directory
    os.makedirs(args.output, exist_ok=True)

    # Process each cluster
    cluster_files = []
    for topic, cluster_tweets in clusters.items():
        if len(cluster_tweets) < 5:
            print(f"  Skipping {topic} (too few tweets)")
            continue

        # Prioritize tweets
        prioritized = prioritize_tweets(cluster_tweets, args.max_per_cluster)

        # Format for LLM
        prompt = format_cluster_for_llm(topic, prioritized)

        # Save
        filename = f"{topic}.txt"
        filepath = os.path.join(args.output, filename)
        with open(filepath, "w") as f:
            f.write(prompt)

        cluster_files.append({
            "topic": topic,
            "file": filepath,
            "tweet_count": len(cluster_tweets),
            "prioritized_count": len(prioritized)
        })

        print(f"  Wrote {filepath} ({len(prioritized)} tweets)")

    # Save cluster manifest
    manifest = {
        "stats": stats,
        "clusters": cluster_files
    }
    manifest_path = os.path.join(args.output, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\nManifest saved to {manifest_path}")
    print(f"\nNext step: Run LLM summarization on each cluster file")
    print(f"Then merge summaries using the meta-prompt generator")


if __name__ == "__main__":
    main()
