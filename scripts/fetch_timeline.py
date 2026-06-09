#!/usr/bin/env python3
"""
Fetch X/Twitter home timeline using internal GraphQL API.

Usage:
    # Set auth tokens (extract from browser cookies)
    export X_AUTH_TOKEN="your_auth_token"
    export X_CT0="your_ct0_token"

    # Run
    python fetch_timeline.py --count 500 --output timeline.json

See docs/x-api-reverse-engineering.md for how to get auth tokens.
"""

import urllib.request
import urllib.parse
import json
import time
import ssl
import os
import argparse
from datetime import datetime

# Auth from environment
AUTH_TOKEN = os.environ.get("X_AUTH_TOKEN", "")
CT0 = os.environ.get("X_CT0", "")

# Static bearer token (same for all users - identifies web client)
BEARER = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

ENDPOINT = "https://x.com/i/api/graphql/GP_SvUI4lAFrt6UyEnkAGA/HomeTimeline"

# Required feature flags (API returns 400 if missing)
FEATURES = {
    "rweb_video_screen_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": False,
    "rweb_tipjar_consumption_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "responsive_web_jetfuel_frame": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "responsive_web_grok_annotations_enabled": False,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "responsive_web_grok_show_grok_translated_post": True,
    "responsive_web_grok_analysis_button_from_backend": True,
    "post_ctas_fetch_enabled": True,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": False,
    "responsive_web_enhance_cards_enabled": False
}


def get_headers(auth_token: str, ct0: str) -> dict:
    """Build request headers with auth tokens."""
    return {
        "Authorization": f"Bearer {BEARER}",
        "X-Csrf-Token": ct0,
        "Cookie": f"auth_token={auth_token}; ct0={ct0}",
        "Content-Type": "application/json",
        "X-Twitter-Active-User": "yes",
        "X-Twitter-Auth-Type": "OAuth2Session",
        "X-Twitter-Client-Language": "en",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }


def extract_user(result: dict) -> dict:
    """Extract user info from nested API response structure."""
    core = result.get("core", {})
    user_results = core.get("user_results", {}).get("result", {})

    # Primary location (newer structure)
    user_core = user_results.get("core", {})
    user_legacy = user_results.get("legacy", {})

    if user_core.get("screen_name"):
        return {
            "name": user_core.get("name"),
            "screen_name": user_core.get("screen_name"),
            "verified": user_results.get("is_blue_verified", False),
            "followers": user_legacy.get("followers_count", 0)
        }

    # Fallback to legacy structure
    if user_legacy.get("screen_name"):
        return {
            "name": user_legacy.get("name"),
            "screen_name": user_legacy.get("screen_name"),
            "verified": user_results.get("is_blue_verified", False),
            "followers": user_legacy.get("followers_count", 0)
        }

    return {"name": "Unknown", "screen_name": "unknown", "verified": False, "followers": 0}


def fetch_timeline(target: int, auth_token: str, ct0: str, delay: float = 0.3) -> list:
    """
    Fetch tweets from home timeline.

    Args:
        target: Number of tweets to fetch
        auth_token: X auth_token cookie value
        ct0: X ct0 cookie value
        delay: Seconds between requests (rate limiting)

    Returns:
        List of normalized tweet dictionaries
    """
    headers = get_headers(auth_token, ct0)
    ctx = ssl.create_default_context()
    all_tweets = []
    cursor = None

    print(f"Fetching {target} tweets from timeline...")

    while len(all_tweets) < target:
        variables = {"count": 20, "includePromotedContent": False, "withCommunity": True}
        if cursor:
            variables["cursor"] = cursor

        params = urllib.parse.urlencode({
            "variables": json.dumps(variables),
            "features": json.dumps(FEATURES)
        })

        url = f"{ENDPOINT}?{params}"

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                data = json.loads(resp.read().decode())

            instructions = data.get("data", {}).get("home", {}).get("home_timeline_urt", {}).get("instructions", [])

            new_cursor = None
            tweets_this_batch = 0

            for instruction in instructions:
                if instruction.get("type") == "TimelineAddEntries":
                    entries = instruction.get("entries", [])
                    for entry in entries:
                        entry_id = entry.get("entryId", "")

                        if "cursor-bottom" in entry_id:
                            new_cursor = entry.get("content", {}).get("value")
                            continue

                        if not entry_id.startswith("tweet-"):
                            continue

                        content = entry.get("content", {})
                        item_content = content.get("itemContent", {})
                        tweet_results = item_content.get("tweet_results", {})
                        result = tweet_results.get("result", {})

                        if result.get("__typename") == "TweetWithVisibilityResults":
                            result = result.get("tweet", {})

                        legacy = result.get("legacy", {})

                        if legacy:
                            user_info = extract_user(result)

                            tweet_info = {
                                "id": legacy.get("id_str"),
                                "text": legacy.get("full_text", ""),
                                "created_at": legacy.get("created_at"),
                                "user": user_info,
                                "metrics": {
                                    "likes": legacy.get("favorite_count", 0),
                                    "retweets": legacy.get("retweet_count", 0),
                                    "replies": legacy.get("reply_count", 0),
                                    "views": result.get("views", {}).get("count", "0")
                                },
                                "is_retweet": legacy.get("retweeted_status_result") is not None,
                                "is_quote": legacy.get("is_quote_status", False)
                            }
                            all_tweets.append(tweet_info)
                            tweets_this_batch += 1

            print(f"  Fetched {tweets_this_batch} tweets, total: {len(all_tweets)}")

            if not new_cursor:
                print("No more pages available")
                break

            cursor = new_cursor
            time.sleep(delay)

        except urllib.error.HTTPError as e:
            print(f"HTTP Error: {e.code}")
            error_body = e.read().decode()[:500]
            print(error_body)
            if e.code == 401:
                print("\nAuth tokens expired. Re-extract from browser.")
            break
        except Exception as e:
            print(f"Exception: {e}")
            import traceback
            traceback.print_exc()
            break

    return all_tweets


def main():
    parser = argparse.ArgumentParser(description="Fetch X/Twitter home timeline")
    parser.add_argument("--count", type=int, default=500, help="Number of tweets to fetch")
    parser.add_argument("--output", type=str, default=None, help="Output JSON file path")
    parser.add_argument("--delay", type=float, default=0.3, help="Delay between requests (seconds)")
    args = parser.parse_args()

    if not AUTH_TOKEN or not CT0:
        print("Error: Set X_AUTH_TOKEN and X_CT0 environment variables")
        print("  export X_AUTH_TOKEN='your_auth_token'")
        print("  export X_CT0='your_ct0_token'")
        print("\nSee docs/x-api-reverse-engineering.md for how to get these.")
        exit(1)

    tweets = fetch_timeline(args.count, AUTH_TOKEN, CT0, args.delay)

    # Default output filename with timestamp
    if args.output:
        output_file = args.output
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f"data/timeline_{timestamp}.json"
        os.makedirs("data", exist_ok=True)

    with open(output_file, "w") as f:
        json.dump(tweets, f, indent=2)

    print(f"\nSaved {len(tweets)} tweets to {output_file}")

    # Summary stats
    if tweets:
        total_likes = sum(t["metrics"]["likes"] for t in tweets)
        unique_users = len(set(t["user"]["screen_name"] for t in tweets))
        retweets = sum(1 for t in tweets if t["is_retweet"])

        print(f"\nStats:")
        print(f"  Unique users: {unique_users}")
        print(f"  Total likes: {total_likes:,}")
        print(f"  Retweets: {retweets} ({100*retweets/len(tweets):.1f}%)")

        print(f"\nTop 5 by engagement:")
        sorted_tweets = sorted(tweets, key=lambda x: x["metrics"]["likes"], reverse=True)[:5]
        for t in sorted_tweets:
            text = t["text"][:50].replace("\n", " ")
            print(f"  @{t['user']['screen_name']}: {text}... ({t['metrics']['likes']:,} likes)")


if __name__ == "__main__":
    main()
