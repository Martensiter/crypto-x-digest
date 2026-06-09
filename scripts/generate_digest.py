#!/usr/bin/env python3
"""
Generate Twitter digest using clustered summarization.

Pipeline:
1. Fetch tweets (or use existing data)
2. Extract topics & deduplicate
3. Filter promotional content & score news relevance
4. Cluster by topic
5. Summarize each cluster (parallel LLM calls)
6. Merge into final digest
7. (Optional) Generate TTS audio briefing

Usage:
    # Full pipeline
    python generate_digest.py --fetch --count 5000

    # From existing data
    python generate_digest.py --input data/x-timeline-5000.json

    # With strict filtering (removes more promo, keeps only newsworthy)
    python generate_digest.py --input data/tweets.json --strict-filter

    # Skip promotional filter
    python generate_digest.py --input data/tweets.json --skip-filter

    # Generate audio briefing after digest
    python generate_digest.py --input data/tweets.json --audio
"""

import json
import os
import subprocess
import argparse
from datetime import datetime


def run_step(name: str, cmd: list) -> bool:
    """Run a pipeline step."""
    print(f"\n{'='*60}")
    print(f"STEP: {name}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, capture_output=False)
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="Generate Twitter digest")
    parser.add_argument("--fetch", action="store_true", help="Fetch fresh tweets")
    parser.add_argument("--count", type=int, default=1000, help="Number of tweets to fetch")
    parser.add_argument("--input", help="Use existing tweets JSON file")
    parser.add_argument("--output", default="digest.md", help="Output digest file")
    parser.add_argument("--skip-filter", action="store_true",
                        help="Skip promotional content filter")
    parser.add_argument("--strict-filter", action="store_true",
                        help="Strict filtering (removes more promo, requires higher news score)")
    parser.add_argument("--audio", action="store_true",
                        help="Generate TTS audio briefing from digest")
    parser.add_argument("--audio-voice", default="nova",
                        help="TTS voice (alloy, echo, fable, onyx, nova, shimmer)")
    args = parser.parse_args()

    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(scripts_dir)
    data_dir = os.path.join(project_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Step 1: Fetch or use existing
    if args.fetch:
        tweets_file = os.path.join(data_dir, f"timeline_{timestamp}.json")
        if not run_step("Fetch tweets", [
            "python", os.path.join(scripts_dir, "fetch_timeline.py"),
            "--count", str(args.count),
            "--output", tweets_file
        ]):
            print("Failed to fetch tweets")
            return 1
    elif args.input:
        tweets_file = args.input
    else:
        # Find most recent
        files = [f for f in os.listdir(data_dir) if f.endswith(".json") and "timeline" in f]
        if not files:
            print("No tweets file found. Use --fetch or --input")
            return 1
        tweets_file = os.path.join(data_dir, sorted(files)[-1])
        print(f"Using existing: {tweets_file}")

    # Step 2: Extract topics
    enriched_file = os.path.join(data_dir, "enriched.json")
    if not run_step("Extract topics", [
        "python", os.path.join(scripts_dir, "extract_topics.py"),
        tweets_file,
        "--output", enriched_file
    ]):
        print("Failed to extract topics")
        return 1

    # Step 3: Filter promotional content & score news relevance
    if args.skip_filter:
        filtered_file = enriched_file
        print("\n(Skipping promotional filter)")
    else:
        filtered_file = os.path.join(data_dir, "filtered.json")
        filter_cmd = [
            "python", os.path.join(scripts_dir, "filter_promotional.py"),
            enriched_file,
            "--output", filtered_file,
            "--stats",
        ]
        if args.strict_filter:
            filter_cmd.append("--strict")

        if not run_step("Filter promotional content", filter_cmd):
            print("Failed to filter (falling back to unfiltered)")
            filtered_file = enriched_file

    # Step 4: Cluster
    clusters_dir = os.path.join(data_dir, "clusters")
    if not run_step("Cluster tweets", [
        "python", os.path.join(scripts_dir, "cluster_and_summarize.py"),
        filtered_file,
        "--output", clusters_dir
    ]):
        print("Failed to cluster")
        return 1

    # Step 5: Show what's ready for summarization
    manifest_file = os.path.join(clusters_dir, "manifest.json")
    with open(manifest_file) as f:
        manifest = json.load(f)

    print(f"\n{'='*60}")
    print("CLUSTERS READY FOR SUMMARIZATION")
    print(f"{'='*60}")

    for cluster in manifest["clusters"]:
        print(f"  {cluster['topic']}: {cluster['prioritized_count']} tweets -> {cluster['file']}")

    print(f"\n{'='*60}")
    print("NEXT STEPS")
    print(f"{'='*60}")
    print("""
The clusters are now ready for LLM summarization.

Option A: Use Claude Code subagents (recommended)
  - Run 9 parallel Haiku agents, one per cluster
  - Merge summaries into final digest

Option B: Use Claude API directly
  - Call claude-3-haiku for each cluster file
  - Cheaper and faster than full Opus

Option C: Manual review
  - Read cluster files in data/clusters/
  - Each file has top 100 tweets by engagement

To summarize a cluster with Claude Code:
  1. Read the cluster file (e.g., data/clusters/ai.txt)
  2. Ask Claude to summarize with narrative and source links
  3. Repeat for each cluster
  4. Merge into final digest, save as digest.md
  5. Post: python scripts/post_digest.py digest.md (see config/post_destinations.example.yaml)
""")

    # Step 6: Generate TTS audio (if requested)
    if args.audio:
        digest_path = os.path.join(project_dir, args.output)
        if os.path.exists(digest_path):
            audio_file = os.path.join(data_dir, f"briefing_{timestamp}.mp3")
            audio_cmd = [
                "python", os.path.join(scripts_dir, "generate_audio.py"),
                digest_path,
                "--output", audio_file,
                "--voice", args.audio_voice,
            ]
            if run_step("Generate TTS audio briefing", audio_cmd):
                print(f"\nAudio briefing saved to: {audio_file}")
                print("Post with audio: python scripts/post_digest.py digest.md --audio " + audio_file)
            else:
                print("Audio generation failed (digest still available as text)")
        else:
            print(f"\nDigest file not found at {digest_path}")
            print("Generate the digest first, then run with --audio")

    return 0


if __name__ == "__main__":
    exit(main())
