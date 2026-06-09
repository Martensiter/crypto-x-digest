# Reverse Engineering X/Twitter's Internal GraphQL API

**Date:** January 2026
**Purpose:** Fetch authenticated user's home timeline without official API access

---

## Overview

X/Twitter's web client uses internal GraphQL endpoints that aren't part of the official API. By extracting authentication tokens from a browser session, we can make the same requests programmatically.

**Endpoint:** `https://x.com/i/api/graphql/GP_SvUI4lAFrt6UyEnkAGA/HomeTimeline`

---

## Step 1: Extract Authentication Tokens

You need two tokens from an authenticated browser session:

### Method: Browser DevTools

1. Open x.com and log in
2. Open DevTools → Application → Cookies → `https://x.com`
3. Find and copy:
   - **`auth_token`** — Your session token (40 char hex)
   - **`ct0`** — CSRF token (128 char hex)

### Method: Using agent-browser with CDP

If you have agent-browser connected to Chrome via CDP:

```bash
# Launch Chrome with debugging enabled
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-debug-profile

# Connect agent-browser
agent-browser --cdp-url http://localhost:9222

# Navigate and extract cookies
goto x.com
# After logging in manually:
execute document.cookie
```

---

## Step 2: Required Headers

```python
BEARER = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

headers = {
    "Authorization": f"Bearer {BEARER}",      # Static bearer token (same for everyone)
    "X-Csrf-Token": CT0,                       # Your ct0 cookie value
    "Cookie": f"auth_token={AUTH_TOKEN}; ct0={CT0}",
    "Content-Type": "application/json",
    "X-Twitter-Active-User": "yes",
    "X-Twitter-Auth-Type": "OAuth2Session",
    "X-Twitter-Client-Language": "en",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}
```

**Note:** The Bearer token is the same for all users — it identifies the web client, not you. Your identity comes from `auth_token`.

---

## Step 3: Required Feature Flags

The API requires a `features` JSON object with ~35 boolean flags. If any required flag is missing, you get a 400 error listing missing flags.

```python
features = {
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
```

**How we discovered these:** Made a request with minimal features, got a 400 error listing required ones, added them iteratively.

---

## Step 4: Making the Request

### Request Structure

```python
variables = {
    "count": 20,                    # Tweets per page (max ~20)
    "includePromotedContent": False,  # Skip ads
    "withCommunity": True
}

# For pagination, add cursor:
variables["cursor"] = "DAABCgABGPWRp..."  # From previous response

params = urllib.parse.urlencode({
    "variables": json.dumps(variables),
    "features": json.dumps(features)
})

url = f"{ENDPOINT}?{params}"
```

### Full Request Example

```bash
curl -X GET \
  'https://x.com/i/api/graphql/GP_SvUI4lAFrt6UyEnkAGA/HomeTimeline?variables={"count":20,"includePromotedContent":false}&features={...}' \
  -H 'Authorization: Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA' \
  -H 'X-Csrf-Token: YOUR_CT0_TOKEN' \
  -H 'Cookie: auth_token=YOUR_AUTH_TOKEN; ct0=YOUR_CT0_TOKEN'
```

---

## Step 5: Parsing the Response

### Response Structure

```
data
└── home
    └── home_timeline_urt
        └── instructions[]
            └── {type: "TimelineAddEntries"}
                └── entries[]
                    ├── {entryId: "tweet-123...", content: {...}}
                    ├── {entryId: "tweet-456...", content: {...}}
                    └── {entryId: "cursor-bottom-...", content: {value: "NEXT_CURSOR"}}
```

### Extracting Tweets

```python
instructions = data["data"]["home"]["home_timeline_urt"]["instructions"]

for instruction in instructions:
    if instruction["type"] == "TimelineAddEntries":
        for entry in instruction["entries"]:
            entry_id = entry["entryId"]

            # Pagination cursor
            if "cursor-bottom" in entry_id:
                next_cursor = entry["content"]["value"]
                continue

            # Skip non-tweets
            if not entry_id.startswith("tweet-"):
                continue

            # Extract tweet data
            result = entry["content"]["itemContent"]["tweet_results"]["result"]

            # Handle visibility wrapper
            if result["__typename"] == "TweetWithVisibilityResults":
                result = result["tweet"]

            legacy = result["legacy"]  # Contains actual tweet data
```

### Extracting User Info

User data is nested deeply and structure varies:

```python
def extract_user(result):
    core = result.get("core", {})
    user_results = core.get("user_results", {}).get("result", {})

    # Primary location (newer structure)
    user_core = user_results.get("core", {})
    if user_core.get("screen_name"):
        return {
            "name": user_core["name"],
            "screen_name": user_core["screen_name"],
            "verified": user_results.get("is_blue_verified", False),
            "followers": user_results.get("legacy", {}).get("followers_count", 0)
        }

    # Fallback location (older structure)
    user_legacy = user_results.get("legacy", {})
    return {
        "name": user_legacy.get("name"),
        "screen_name": user_legacy.get("screen_name"),
        ...
    }
```

---

## Step 6: Pagination

The response includes cursor entries for pagination:

```python
cursor = None
all_tweets = []

while len(all_tweets) < target_count:
    variables = {"count": 20, "includePromotedContent": False}
    if cursor:
        variables["cursor"] = cursor

    # ... make request ...

    # Find next cursor in entries
    for entry in entries:
        if "cursor-bottom" in entry["entryId"]:
            cursor = entry["content"]["value"]

    time.sleep(0.3)  # Rate limiting
```

---

## Rate Limits & Best Practices

- **Batch size:** 20 tweets per request (API limit)
- **Rate:** ~0.3s between requests works reliably
- **Sessions:** Tokens expire, refresh from browser if 401 errors
- **5000 tweets:** Takes ~3-4 minutes with 0.3s delays

---

## Output Schema

Normalized tweet structure:

```json
{
  "id": "2013614189823004938",
  "text": "Full tweet text including URLs...",
  "created_at": "Mon Jan 20 18:45:23 +0000 2026",
  "user": {
    "name": "Display Name",
    "screen_name": "handle",
    "verified": true,
    "followers": 125000
  },
  "metrics": {
    "likes": 4523,
    "retweets": 891,
    "replies": 234,
    "views": "1250000"
  },
  "is_retweet": false,
  "is_quote": false
}
```

---

## Troubleshooting

### 400 Error: Missing Features
Add the features listed in the error response to your `features` object.

### 401 Error: Unauthorized
Tokens expired. Re-extract from browser.

### 403 Error: Forbidden
Account may be rate-limited or suspended. Try different account.

### Empty Responses
Check `instructions` array — may have different `type` values like `TimelineClearCache`.

---

## Discovery Process

How we figured this out:

1. **Network inspection:** Watched XHR requests in DevTools while scrolling timeline
2. **Found GraphQL endpoint:** `https://x.com/i/api/graphql/.../HomeTimeline`
3. **Extracted headers:** Copied request headers from DevTools
4. **Identified auth tokens:** Found `auth_token` and `ct0` cookies are required
5. **Feature flags:** Iteratively added flags based on 400 error messages
6. **Response parsing:** Saved raw responses, mapped nested structure
7. **User extraction:** Found user data location varies, built fallback logic
8. **Pagination:** Found `cursor-bottom` entries contain next page cursor

---

## Files

- `fetch-x-timeline-v2.py` — Main fetching script
- `x-timeline-5000.json` — Sample output (5033 tweets)
- `x-raw-response.json` — Raw API response for debugging

---

## Disclaimer

This uses undocumented internal APIs. X can change these at any time. Use responsibly and respect rate limits.
