#!/usr/bin/env python3
"""
Cluster tweets using semantic embeddings.

Uses sentence-transformers for embeddings and HDBSCAN for clustering.
Finds natural topic groupings without predefined categories.

Usage:
    source .venv/bin/activate
    python scripts/cluster_embeddings.py data/x-timeline-5000.json --output data/clusters_semantic/
"""

import json
import argparse
import os
from collections import Counter

import numpy as np
from sentence_transformers import SentenceTransformer
import hdbscan
from sklearn.cluster import KMeans


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


def engagement_tier(likes: int) -> str:
    """Classify engagement level."""
    if likes >= 50000:
        return "viral"
    elif likes >= 10000:
        return "high"
    elif likes >= 1000:
        return "medium"
    else:
        return "low"


def get_cluster_label(tweets: list) -> str:
    """Generate a label for a cluster based on common words."""
    # Simple approach: find most common meaningful words
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "must", "shall", "can", "need", "dare",
        "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
        "into", "through", "during", "before", "after", "above", "below",
        "between", "under", "again", "further", "then", "once", "here",
        "there", "when", "where", "why", "how", "all", "each", "few", "more",
        "most", "other", "some", "such", "no", "nor", "not", "only", "own",
        "same", "so", "than", "too", "very", "just", "and", "but", "if", "or",
        "because", "until", "while", "this", "that", "these", "those", "what",
        "which", "who", "whom", "its", "it", "i", "you", "he", "she", "we",
        "they", "me", "him", "her", "us", "them", "my", "your", "his", "our",
        "their", "mine", "yours", "hers", "ours", "theirs", "about", "get",
        "like", "https", "co", "t", "rt", "amp", "dont", "im", "ive", "youre",
        "going", "got", "one", "new", "now", "even", "still", "back", "way",
        "make", "think", "know", "see", "come", "take", "want", "look", "use",
        "find", "give", "tell", "work", "call", "try", "ask", "feel", "become",
        "leave", "put", "mean", "keep", "let", "begin", "seem", "help", "show",
        "hear", "play", "run", "move", "live", "believe", "hold", "bring",
        "happen", "write", "provide", "sit", "stand", "lose", "pay", "meet",
        "include", "continue", "set", "learn", "change", "lead", "understand",
        "watch", "follow", "stop", "create", "speak", "read", "allow", "add",
        "spend", "grow", "open", "walk", "win", "offer", "remember", "love",
        "consider", "appear", "buy", "wait", "serve", "die", "send", "expect",
        "build", "stay", "fall", "cut", "reach", "kill", "remain", "people",
        "year", "years", "day", "days", "time", "thing", "things", "really",
        "much", "many", "well", "also", "any", "right", "up", "down", "out",
        "over", "off", "say", "says", "said", "saying"
    }

    word_counts = Counter()
    for tweet in tweets:
        text = tweet.get("text", "").lower()
        # Simple tokenization
        words = [w.strip(".,!?\"'()[]{}:;") for w in text.split()]
        words = [w for w in words if len(w) > 2 and w not in stopwords and not w.startswith("@") and not w.startswith("http")]
        word_counts.update(words)

    # Get top 3 words
    top_words = [word for word, _ in word_counts.most_common(5)]
    return " / ".join(top_words[:3]) if top_words else "misc"


def format_cluster_for_llm(cluster_id: int, label: str, tweets: list) -> str:
    """Format cluster as prompt for LLM summarization."""
    lines = [
        f"# Cluster {cluster_id}: {label}",
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


def main():
    parser = argparse.ArgumentParser(description="Cluster tweets using embeddings")
    parser.add_argument("input", help="Input JSON file with tweets")
    parser.add_argument("--output", "-o", help="Output directory", default="data/clusters_semantic")
    parser.add_argument("--min-cluster-size", type=int, default=15, help="Minimum cluster size for HDBSCAN")
    parser.add_argument("--model", default="all-MiniLM-L6-v2", help="Sentence transformer model")
    parser.add_argument("--algorithm", choices=["hdbscan", "kmeans"], default="kmeans", help="Clustering algorithm")
    parser.add_argument("--n-clusters", type=int, default=20, help="Number of clusters for K-means")
    args = parser.parse_args()

    # Load tweets
    print(f"Loading tweets from {args.input}...")
    with open(args.input) as f:
        tweets = json.load(f)

    print(f"Loaded {len(tweets)} tweets")

    # Deduplicate
    tweets = deduplicate_tweets(tweets)
    print(f"After deduplication: {len(tweets)} unique tweets")

    # Filter retweets
    original_tweets = [t for t in tweets if not t.get("is_retweet")]
    print(f"Original tweets (no RTs): {len(original_tweets)}")

    # Extract texts
    texts = [t.get("text", "") for t in original_tweets]

    # Generate embeddings
    print(f"\nLoading model: {args.model}...")
    model = SentenceTransformer(args.model)

    print("Generating embeddings...")
    embeddings = model.encode(texts, show_progress_bar=True)
    print(f"Embeddings shape: {embeddings.shape}")

    # Cluster
    if args.algorithm == "hdbscan":
        print(f"\nClustering with HDBSCAN (min_cluster_size={args.min_cluster_size})...")
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=args.min_cluster_size,
            min_samples=5,
            metric='euclidean',
            cluster_selection_method='eom'
        )
        labels = clusterer.fit_predict(embeddings)
    else:
        print(f"\nClustering with K-means (n_clusters={args.n_clusters})...")
        clusterer = KMeans(n_clusters=args.n_clusters, random_state=42, n_init=10)
        labels = clusterer.fit_predict(embeddings)

    # Analyze clusters
    unique_labels = set(labels)
    n_clusters = len(unique_labels) - (1 if -1 in unique_labels else 0)
    n_noise = list(labels).count(-1)

    print(f"\nFound {n_clusters} clusters")
    print(f"Noise points (unclustered): {n_noise} ({100*n_noise/len(labels):.1f}%)")

    # Group tweets by cluster
    clusters = {}
    for i, label in enumerate(labels):
        if label == -1:
            continue  # Skip noise
        if label not in clusters:
            clusters[label] = []
        clusters[label].append(original_tweets[i])

    # Sort clusters by size
    sorted_clusters = sorted(clusters.items(), key=lambda x: -len(x[1]))

    # Create output directory
    os.makedirs(args.output, exist_ok=True)

    # Process each cluster
    print(f"\nCluster breakdown:")
    cluster_info = []

    for cluster_id, cluster_tweets in sorted_clusters:
        # Sort by engagement within cluster
        cluster_tweets.sort(key=lambda t: t.get("metrics", {}).get("likes", 0), reverse=True)

        # Generate label
        label = get_cluster_label(cluster_tweets)

        # Count engagement tiers
        tiers = Counter(engagement_tier(t.get("metrics", {}).get("likes", 0)) for t in cluster_tweets)

        print(f"  Cluster {cluster_id}: {len(cluster_tweets)} tweets - \"{label}\"")
        print(f"    Viral: {tiers.get('viral', 0)}, High: {tiers.get('high', 0)}, Medium: {tiers.get('medium', 0)}, Low: {tiers.get('low', 0)}")

        # Prioritize: take top 100 by engagement
        prioritized = cluster_tweets[:100]

        # Format for LLM
        prompt = format_cluster_for_llm(cluster_id, label, prioritized)

        # Save
        filename = f"cluster_{cluster_id:02d}_{label.replace(' / ', '_').replace(' ', '_')[:30]}.txt"
        filepath = os.path.join(args.output, filename)
        with open(filepath, "w") as f:
            f.write(prompt)

        cluster_info.append({
            "cluster_id": int(cluster_id),  # Convert numpy int64 to int
            "label": label,
            "tweet_count": len(cluster_tweets),
            "prioritized_count": len(prioritized),
            "file": filepath,
            "top_tweet": {
                "user": cluster_tweets[0]["user"]["screen_name"],
                "likes": int(cluster_tweets[0]["metrics"]["likes"]),
                "text": cluster_tweets[0]["text"][:100]
            } if cluster_tweets else None
        })

    # Save manifest
    manifest = {
        "total_tweets": len(original_tweets),
        "clustered_tweets": len(original_tweets) - n_noise,
        "noise_tweets": n_noise,
        "n_clusters": n_clusters,
        "model": args.model,
        "min_cluster_size": args.min_cluster_size,
        "clusters": cluster_info
    }

    manifest_path = os.path.join(args.output, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\nManifest saved to {manifest_path}")
    print(f"Cluster files saved to {args.output}/")


if __name__ == "__main__":
    main()
