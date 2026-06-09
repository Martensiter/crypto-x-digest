#!/usr/bin/env python3
"""
Hybrid tweet clustering: lexical-anchored semantic clustering.

Combines dense embeddings with sparse TF-IDF features to prevent "vibe clusters"
and form clusters around topics rather than writing style.

Based on 2025-2026 research recommendations:
- Dense: bge-small-en-v1.5 or e5-small-v2
- Sparse: TF-IDF on cleaned text + entities
- Clustering: HDBSCAN with soft assignment
- Labels: c-TF-IDF + entities + MMR

Usage:
    python scripts/cluster_hybrid.py data/tweets.json --output data/clusters_hybrid/
"""

import json
import argparse
import os
import re
from collections import Counter
from typing import Optional

import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize
from sklearn.metrics import silhouette_score
import hdbscan
from umap import UMAP


# ============================================================================
# PREPROCESSING
# ============================================================================

def preprocess_tweet(text: str) -> dict:
    """
    Preprocess tweet for clustering.

    Returns dict with:
        - clean_text: normalized text for embedding
        - hashtags: list of hashtags
        - cashtags: list of cashtags ($BTC etc)
        - mentions: list of @mentions (normalized)
        - entities: extracted proper nouns / notable terms
    """
    original = text

    # Extract before cleaning
    hashtags = re.findall(r'#(\w+)', text)
    cashtags = re.findall(r'\$([A-Z]{2,5})', text)
    mentions = re.findall(r'@(\w+)', text)

    # Strip URLs
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r't\.co/\S+', '', text)

    # Strip RT prefix
    text = re.sub(r'^RT\s+', '', text)
    text = re.sub(r'\bvia\s+@\w+', '', text)

    # Normalize mentions to @USER (don't let handles dominate embeddings)
    text = re.sub(r'@\w+', '@USER', text)

    # Keep hashtags but remove # symbol for cleaner embedding
    text = re.sub(r'#(\w+)', r'\1', text)

    # Remove excessive whitespace
    text = ' '.join(text.split())

    # Extract potential entities (capitalized words, excluding common words)
    common_words = {'The', 'This', 'That', 'What', 'When', 'Where', 'Why', 'How',
                    'I', 'We', 'They', 'You', 'It', 'A', 'An', 'And', 'Or', 'But',
                    'Just', 'Now', 'New', 'Today', 'Here', 'So', 'If', 'My', 'Your'}

    # Find capitalized words that might be entities
    words = original.split()
    entities = []
    for i, word in enumerate(words):
        # Skip if at start of sentence (likely just capitalized normally)
        clean_word = re.sub(r'[^\w]', '', word)
        if clean_word and clean_word[0].isupper() and clean_word not in common_words:
            if len(clean_word) > 1:
                entities.append(clean_word)

    # Also add hashtags and cashtags as entities
    entities.extend(hashtags)
    entities.extend(cashtags)

    return {
        'clean_text': text.strip(),
        'hashtags': hashtags,
        'cashtags': cashtags,
        'mentions': mentions,
        'entities': list(set(entities))
    }


def preprocess_tweets(tweets: list) -> list:
    """Preprocess all tweets, adding preprocessed fields."""
    for tweet in tweets:
        text = tweet.get('text', '')
        preprocessed = preprocess_tweet(text)
        tweet['preprocessed'] = preprocessed
    return tweets


# ============================================================================
# EMBEDDINGS
# ============================================================================

def get_dense_embeddings(tweets: list, model_name: str = "BAAI/bge-small-en-v1.5") -> np.ndarray:
    """Generate dense embeddings using sentence transformer."""
    print(f"Loading dense model: {model_name}...")
    model = SentenceTransformer(model_name)

    texts = [t['preprocessed']['clean_text'] for t in tweets]

    print("Generating dense embeddings...")
    embeddings = model.encode(texts, show_progress_bar=True, normalize_embeddings=True)

    return embeddings


def get_sparse_embeddings(tweets: list, min_df: int = 2, max_df: float = 0.8) -> tuple:
    """
    Generate sparse TF-IDF embeddings.

    Includes cleaned text + hashtags + entities for lexical anchoring.
    """
    # Build text that includes entities prominently
    texts = []
    for t in tweets:
        prep = t['preprocessed']
        # Combine clean text with entities (weighted by repetition)
        entity_str = ' '.join(prep['entities'] * 2)  # Repeat entities for weight
        hashtag_str = ' '.join(prep['hashtags'] * 2)
        combined = f"{prep['clean_text']} {entity_str} {hashtag_str}"
        texts.append(combined)

    print("Generating sparse TF-IDF embeddings...")
    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),
        min_df=min_df,
        max_df=max_df,
        stop_words='english',
        max_features=5000
    )

    sparse_matrix = vectorizer.fit_transform(texts)

    # Convert to dense and normalize
    sparse_dense = sparse_matrix.toarray()
    sparse_dense = normalize(sparse_dense, norm='l2')

    return sparse_dense, vectorizer


def create_hybrid_embeddings(dense: np.ndarray, sparse: np.ndarray, lambda_weight: float = 0.35) -> np.ndarray:
    """
    Combine dense and sparse embeddings.

    Final vector = [dense ; λ * sparse]
    """
    print(f"Creating hybrid embeddings (λ={lambda_weight})...")

    # Scale sparse by lambda
    sparse_weighted = sparse * lambda_weight

    # Concatenate
    hybrid = np.hstack([dense, sparse_weighted])

    # Normalize final hybrid vector
    hybrid = normalize(hybrid, norm='l2')

    print(f"Hybrid embedding shape: {hybrid.shape}")
    return hybrid


# ============================================================================
# DIMENSIONALITY REDUCTION
# ============================================================================

def reduce_with_umap(embeddings: np.ndarray,
                     n_components: int = 10,
                     n_neighbors: int = 15,
                     min_dist: float = 0.0,
                     random_state: int = 42) -> np.ndarray:
    """
    Reduce embeddings with UMAP for better clustering.

    UMAP preserves local structure while reducing dimensionality,
    making HDBSCAN work better on high-dimensional data.
    """
    print(f"Reducing dimensions with UMAP ({embeddings.shape[1]} -> {n_components})...")

    umap_model = UMAP(
        n_components=n_components,
        n_neighbors=n_neighbors,
        min_dist=min_dist,
        metric='cosine',
        random_state=random_state
    )

    reduced = umap_model.fit_transform(embeddings)
    print(f"Reduced embeddings shape: {reduced.shape}")
    return reduced


# ============================================================================
# CLUSTERING
# ============================================================================

def cluster_hdbscan(embeddings: np.ndarray,
                    min_cluster_size: int = 10,
                    min_samples: int = 5,
                    cluster_selection_method: str = 'eom') -> tuple:
    """
    Cluster using HDBSCAN.

    Returns (labels, probabilities, clusterer)
    """
    print(f"Clustering with HDBSCAN (min_cluster_size={min_cluster_size}, min_samples={min_samples}, method={cluster_selection_method})...")

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        metric='euclidean',  # On L2-normalized vectors, this ≈ cosine
        cluster_selection_method=cluster_selection_method,
        prediction_data=True
    )

    labels = clusterer.fit_predict(embeddings)
    probabilities = clusterer.probabilities_

    return labels, probabilities, clusterer


def compute_cluster_centroids(embeddings: np.ndarray, labels: np.ndarray) -> dict:
    """Compute centroid for each cluster."""
    centroids = {}
    unique_labels = set(labels) - {-1}  # Exclude noise

    for label in unique_labels:
        mask = labels == label
        cluster_embeddings = embeddings[mask]
        centroid = cluster_embeddings.mean(axis=0)
        centroid = centroid / np.linalg.norm(centroid)  # Normalize
        centroids[label] = centroid

    return centroids


def soft_assign_secondary_clusters(embeddings: np.ndarray,
                                   labels: np.ndarray,
                                   centroids: dict,
                                   sim_threshold: float = 0.80,
                                   delta_threshold: float = 0.03) -> list:
    """
    Assign secondary cluster memberships for boundary tweets.

    A tweet gets secondary assignment if:
    - similarity to another centroid >= sim_threshold, OR
    - similarity within delta_threshold of best cluster
    """
    secondary_assignments = []

    for i, (emb, primary_label) in enumerate(zip(embeddings, labels)):
        secondary = []

        if primary_label == -1:  # Noise point
            secondary_assignments.append([])
            continue

        # Compute similarity to all centroids
        sims = {}
        for label, centroid in centroids.items():
            sim = np.dot(emb, centroid)
            sims[label] = sim

        primary_sim = sims.get(primary_label, 0)

        # Find secondary assignments
        for label, sim in sims.items():
            if label == primary_label:
                continue

            # Assign if above threshold OR within delta of primary
            if sim >= sim_threshold or (primary_sim - sim) <= delta_threshold:
                secondary.append((label, sim))

        # Sort by similarity and take top 2
        secondary.sort(key=lambda x: -x[1])
        secondary_assignments.append([s[0] for s in secondary[:2]])

    return secondary_assignments


# ============================================================================
# LABELING
# ============================================================================

def get_cluster_ctfidf_keywords(tweets: list, labels: np.ndarray, vectorizer, top_n: int = 10) -> dict:
    """
    Extract c-TF-IDF keywords per cluster.

    c-TF-IDF: term frequency in cluster / document frequency across all clusters
    """
    from sklearn.feature_extraction.text import CountVectorizer

    unique_labels = set(labels) - {-1}

    # Get feature names from original vectorizer
    feature_names = vectorizer.get_feature_names_out()

    # Build cluster documents (concatenate all tweets in cluster)
    cluster_docs = {}
    for label in unique_labels:
        mask = labels == label
        cluster_tweets = [tweets[i] for i in range(len(tweets)) if mask[i]]
        combined_text = ' '.join([t['preprocessed']['clean_text'] for t in cluster_tweets])
        cluster_docs[label] = combined_text

    # Compute TF-IDF on cluster-level documents
    # Add custom stopwords including our normalized tokens
    custom_stops = list(TfidfVectorizer(stop_words='english').get_stop_words())
    custom_stops.extend(['user', 'users', 'https', 'http', 'co', 'amp', 'rt', 'via'])

    cluster_vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),
        stop_words=custom_stops,
        max_features=1000
    )

    cluster_texts = [cluster_docs[l] for l in sorted(unique_labels)]
    cluster_tfidf = cluster_vectorizer.fit_transform(cluster_texts)
    cluster_features = cluster_vectorizer.get_feature_names_out()

    # Extract top keywords per cluster
    keywords = {}
    for i, label in enumerate(sorted(unique_labels)):
        scores = cluster_tfidf[i].toarray().flatten()
        top_indices = scores.argsort()[-top_n:][::-1]
        keywords[label] = [(cluster_features[idx], scores[idx]) for idx in top_indices if scores[idx] > 0]

    return keywords


def get_cluster_entities(tweets: list, labels: np.ndarray) -> dict:
    """Extract top entities per cluster."""
    unique_labels = set(labels) - {-1}

    entities = {}
    for label in unique_labels:
        mask = labels == label
        cluster_tweets = [tweets[i] for i in range(len(tweets)) if mask[i]]

        entity_counts = Counter()
        for t in cluster_tweets:
            entity_counts.update(t['preprocessed']['entities'])

        entities[label] = entity_counts.most_common(10)

    return entities


def mmr_select(candidates: list, selected: list, embeddings_dict: dict, lambda_mmr: float = 0.5, top_k: int = 5) -> list:
    """
    Maximal Marginal Relevance selection for diverse keywords.

    Simplified version using string similarity.
    """
    if not candidates:
        return []

    result = []
    remaining = list(candidates)

    while len(result) < top_k and remaining:
        if not result:
            # First selection: highest score
            best = max(remaining, key=lambda x: x[1])
        else:
            # MMR: balance relevance and diversity
            best = None
            best_score = float('-inf')

            for candidate in remaining:
                relevance = candidate[1]

                # Diversity: penalize if similar to already selected
                max_sim = 0
                for selected_item in result:
                    # Simple string overlap as similarity
                    c_words = set(candidate[0].lower().split())
                    s_words = set(selected_item[0].lower().split())
                    if c_words and s_words:
                        overlap = len(c_words & s_words) / max(len(c_words), len(s_words))
                        max_sim = max(max_sim, overlap)

                mmr_score = lambda_mmr * relevance - (1 - lambda_mmr) * max_sim

                if mmr_score > best_score:
                    best_score = mmr_score
                    best = candidate

        if best:
            result.append(best)
            remaining.remove(best)

    return result


def generate_cluster_labels(tweets: list, labels: np.ndarray,
                           keywords: dict, entities: dict) -> dict:
    """
    Generate human-readable cluster labels.

    Template:
    - If strong entity: "<ENTITY>: <top keyphrase>"
    - Else: "<top bigram> / <second bigram>"
    """
    cluster_labels = {}

    for label in keywords.keys():
        cluster_kw = keywords.get(label, [])
        cluster_ent = entities.get(label, [])

        # Apply MMR to keywords
        diverse_kw = mmr_select(cluster_kw, [], {}, top_k=5)

        # Check for strong entity
        strong_entity = None
        if cluster_ent:
            top_entity, top_count = cluster_ent[0]
            # Entity is "strong" if it appears in >30% of cluster tweets
            cluster_size = sum(1 for l in labels if l == label)
            if top_count / cluster_size > 0.3:
                strong_entity = top_entity

        # Generate label
        if strong_entity and diverse_kw:
            main_phrase = diverse_kw[0][0]
            cluster_labels[label] = f"{strong_entity}: {main_phrase}"
        elif diverse_kw:
            phrases = [kw[0] for kw in diverse_kw[:3]]
            cluster_labels[label] = ' / '.join(phrases)
        else:
            cluster_labels[label] = f"cluster_{label}"

    return cluster_labels


# ============================================================================
# VALIDATION METRICS
# ============================================================================

def compute_validation_metrics(embeddings: np.ndarray, labels: np.ndarray,
                               clusterer) -> dict:
    """
    Compute clustering quality metrics.

    Returns dict with:
    - relative_validity: HDBSCAN's DBCV approximation
    - silhouette: silhouette score (if enough clusters)
    - noise_fraction: % of points labeled as noise
    - n_clusters: number of clusters found
    """
    metrics = {}

    # Relative validity (DBCV approximation)
    if hasattr(clusterer, 'relative_validity_'):
        metrics['relative_validity'] = float(clusterer.relative_validity_)
    else:
        metrics['relative_validity'] = None

    # Silhouette score (excluding noise)
    non_noise_mask = labels != -1
    if non_noise_mask.sum() > 1 and len(set(labels[non_noise_mask])) > 1:
        try:
            sil = silhouette_score(embeddings[non_noise_mask], labels[non_noise_mask])
            metrics['silhouette'] = float(sil)
        except:
            metrics['silhouette'] = None
    else:
        metrics['silhouette'] = None

    # Noise fraction
    n_noise = (labels == -1).sum()
    metrics['noise_fraction'] = float(n_noise / len(labels))
    metrics['noise_count'] = int(n_noise)

    # Cluster count
    metrics['n_clusters'] = len(set(labels) - {-1})

    # Quality gate
    rv_ok = metrics['relative_validity'] is None or metrics['relative_validity'] > 0
    sil_ok = metrics['silhouette'] is None or metrics['silhouette'] > 0.05
    noise_ok = metrics['noise_fraction'] < 0.35

    metrics['quality_pass'] = rv_ok and sil_ok and noise_ok

    return metrics


# ============================================================================
# OUTPUT
# ============================================================================

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


def format_viral_highlights(tweets: list) -> str:
    """Format viral highlights (high-engagement unclustered tweets) for digest."""
    lines = [
        "# Viral Highlights",
        "# High-engagement tweets that didn't fit into topic clusters",
        f"# Tweet count: {len(tweets)}",
        "",
        "These are standalone viral tweets - unique content that resonated but doesn't",
        "belong to a trending topic or narrative. Worth including in the digest.",
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


def format_cluster_for_llm(cluster_id: int, label: str, tweets: list,
                           secondary_assignments: list = None) -> str:
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


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Hybrid tweet clustering")
    parser.add_argument("input", help="Input JSON file with tweets")
    parser.add_argument("--output", "-o", help="Output directory", default="data/clusters_hybrid")
    parser.add_argument("--model", default="BAAI/bge-small-en-v1.5",
                        help="Dense embedding model")
    parser.add_argument("--lambda-weight", type=float, default=0.35,
                        help="Weight for sparse embeddings in hybrid")
    parser.add_argument("--min-cluster-size", type=int, default=10,
                        help="HDBSCAN min_cluster_size")
    parser.add_argument("--min-samples", type=int, default=5,
                        help="HDBSCAN min_samples")
    parser.add_argument("--skip-soft-assign", action="store_true",
                        help="Skip secondary cluster assignment")
    parser.add_argument("--umap-dims", type=int, default=10,
                        help="UMAP reduction dimensions (0 to skip)")
    parser.add_argument("--cluster-method", choices=['eom', 'leaf'], default='eom',
                        help="HDBSCAN cluster_selection_method (leaf finds more clusters)")
    args = parser.parse_args()

    # Load tweets
    print(f"Loading tweets from {args.input}...")
    with open(args.input) as f:
        tweets = json.load(f)
    print(f"Loaded {len(tweets)} tweets")

    # Deduplicate
    seen = set()
    unique_tweets = []
    for t in tweets:
        tid = t.get('id')
        if tid and tid not in seen:
            seen.add(tid)
            unique_tweets.append(t)
    tweets = unique_tweets
    print(f"After deduplication: {len(tweets)} unique tweets")

    # Filter retweets
    tweets = [t for t in tweets if not t.get('is_retweet')]
    print(f"After removing retweets: {len(tweets)} original tweets")

    if len(tweets) < 20:
        print("Too few tweets for clustering. Need at least 20.")
        return

    # Step 0: Preprocess
    print("\n=== Preprocessing ===")
    tweets = preprocess_tweets(tweets)

    # Step 1: Dense embeddings
    print("\n=== Dense Embeddings ===")
    dense_embeddings = get_dense_embeddings(tweets, args.model)

    # Step 2: Sparse embeddings
    print("\n=== Sparse Embeddings ===")
    sparse_embeddings, vectorizer = get_sparse_embeddings(tweets)

    # Step 3: Hybrid embeddings
    print("\n=== Hybrid Embeddings ===")
    hybrid_embeddings = create_hybrid_embeddings(
        dense_embeddings, sparse_embeddings, args.lambda_weight
    )

    # Step 3.5: UMAP reduction (crucial for HDBSCAN on high-dim data)
    if args.umap_dims > 0:
        print("\n=== UMAP Reduction ===")
        clustering_embeddings = reduce_with_umap(hybrid_embeddings, n_components=args.umap_dims)
    else:
        clustering_embeddings = hybrid_embeddings

    # Step 4: Cluster
    print("\n=== Clustering ===")
    labels, probabilities, clusterer = cluster_hdbscan(
        clustering_embeddings,
        min_cluster_size=args.min_cluster_size,
        min_samples=args.min_samples,
        cluster_selection_method=args.cluster_method
    )

    n_clusters = len(set(labels) - {-1})
    n_noise = (labels == -1).sum()
    print(f"Found {n_clusters} clusters, {n_noise} noise points ({100*n_noise/len(labels):.1f}%)")

    # Step 5: Compute centroids and soft assignment
    print("\n=== Soft Assignment ===")
    centroids = compute_cluster_centroids(clustering_embeddings, labels)

    if not args.skip_soft_assign:
        secondary = soft_assign_secondary_clusters(
            clustering_embeddings, labels, centroids
        )
        multi_assigned = sum(1 for s in secondary if s)
        print(f"Tweets with secondary assignment: {multi_assigned}")
    else:
        secondary = [[] for _ in labels]

    # Step 6: Generate labels
    print("\n=== Generating Labels ===")
    keywords = get_cluster_ctfidf_keywords(tweets, labels, vectorizer)
    entities = get_cluster_entities(tweets, labels)
    cluster_labels = generate_cluster_labels(tweets, labels, keywords, entities)

    for label, name in sorted(cluster_labels.items()):
        count = (labels == label).sum()
        print(f"  Cluster {label}: {name} ({count} tweets)")

    # Step 7: Validation metrics
    print("\n=== Validation Metrics ===")
    metrics = compute_validation_metrics(clustering_embeddings, labels, clusterer)
    print(f"  Relative validity: {metrics['relative_validity']}")
    print(f"  Silhouette score: {metrics['silhouette']}")
    print(f"  Noise fraction: {metrics['noise_fraction']:.1%}")
    print(f"  Quality gate: {'PASS' if metrics['quality_pass'] else 'FAIL'}")

    # Step 8: Save outputs
    print("\n=== Saving Outputs ===")
    os.makedirs(args.output, exist_ok=True)

    # Group tweets by cluster
    clusters = {}
    for i, label in enumerate(labels):
        if label == -1:
            continue
        if label not in clusters:
            clusters[label] = []
        clusters[label].append((tweets[i], secondary[i], probabilities[i]))

    # Save each cluster
    cluster_info = []
    for label in sorted(clusters.keys()):
        cluster_data = clusters[label]
        cluster_tweets = [d[0] for d in cluster_data]

        # Sort by engagement
        cluster_tweets.sort(key=lambda t: t.get('metrics', {}).get('likes', 0), reverse=True)

        # Take top 100
        prioritized = cluster_tweets[:100]

        # Format for LLM
        cluster_label = cluster_labels.get(label, f"cluster_{label}")
        prompt = format_cluster_for_llm(label, cluster_label, prioritized)

        # Save
        safe_label = re.sub(r'[^\w\s-]', '', cluster_label).replace(' ', '_')[:40]
        filename = f"cluster_{label:02d}_{safe_label}.txt"
        filepath = os.path.join(args.output, filename)

        with open(filepath, 'w') as f:
            f.write(prompt)

        # Cluster info for manifest
        tiers = Counter(engagement_tier(t.get('metrics', {}).get('likes', 0)) for t in cluster_tweets)

        cluster_info.append({
            'cluster_id': int(label),
            'label': cluster_label,
            'tweet_count': len(cluster_tweets),
            'prioritized_count': len(prioritized),
            'file': filepath,
            'keywords': [kw[0] for kw in keywords.get(label, [])[:5]],
            'entities': [e[0] for e in entities.get(label, [])[:5]],
            'engagement': dict(tiers),
            'top_tweet': {
                'user': cluster_tweets[0]['user']['screen_name'],
                'likes': int(cluster_tweets[0]['metrics']['likes']),
                'text': cluster_tweets[0]['text'][:100]
            } if cluster_tweets else None
        })

        print(f"  Saved {filepath}")

    # Handle noise cluster and viral highlights
    noise_tweets = [tweets[i] for i in range(len(tweets)) if labels[i] == -1]
    viral_highlights = []

    if noise_tweets:
        noise_tweets.sort(key=lambda t: t.get('metrics', {}).get('likes', 0), reverse=True)

        # Viral highlights: high-engagement tweets that didn't fit a cluster (>=5000 likes)
        viral_highlights = [t for t in noise_tweets if t.get('metrics', {}).get('likes', 0) >= 5000]

        # Only save if there are notable noise tweets
        notable_noise = [t for t in noise_tweets if t.get('metrics', {}).get('likes', 0) >= 1000]
        if notable_noise:
            prompt = format_cluster_for_llm(-1, "uncategorized (noise)", notable_noise[:50])
            filepath = os.path.join(args.output, "cluster_noise_uncategorized.txt")
            with open(filepath, 'w') as f:
                f.write(prompt)
            print(f"  Saved {filepath} ({len(notable_noise)} notable noise tweets)")

        # Save viral highlights separately
        if viral_highlights:
            viral_prompt = format_viral_highlights(viral_highlights[:20])
            viral_filepath = os.path.join(args.output, "viral_highlights.txt")
            with open(viral_filepath, 'w') as f:
                f.write(viral_prompt)
            print(f"  Saved {viral_filepath} ({len(viral_highlights)} viral highlights)")

    # Build viral highlights info for manifest
    viral_highlights_info = []
    for t in viral_highlights[:20]:
        viral_highlights_info.append({
            'user': t['user']['screen_name'],
            'id': t['id'],
            'likes': int(t['metrics']['likes']),
            'text': t['text'][:200]
        })

    # Save manifest (convert numpy types to Python types)
    manifest = {
        'total_tweets': int(len(tweets)),
        'clustered_tweets': int(len(tweets) - n_noise),
        'noise_tweets': int(n_noise),
        'n_clusters': int(n_clusters),
        'model': args.model,
        'lambda_weight': args.lambda_weight,
        'min_cluster_size': args.min_cluster_size,
        'min_samples': args.min_samples,
        'umap_dims': args.umap_dims,
        'metrics': metrics,
        'clusters': cluster_info,
        'viral_highlights': viral_highlights_info,
        'viral_highlights_file': os.path.join(args.output, "viral_highlights.txt") if viral_highlights else None
    }

    manifest_path = os.path.join(args.output, 'manifest.json')
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)

    print(f"\nManifest saved to {manifest_path}")
    print(f"\n{'='*50}")
    print(f"SUMMARY: {n_clusters} clusters from {len(tweets)} tweets")
    print(f"Quality: {'PASS' if metrics['quality_pass'] else 'FAIL'}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
