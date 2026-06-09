# Twitter Digest Generator

Instructions for Claude Code to generate Twitter timeline digests.

## Quick Command

```
Fetch my tweets for the last N hours and generate a digest
```

## Prerequisites

### Auth Tokens Required

Two environment variables must be set:
- `X_AUTH_TOKEN` — 40-char session token
- `X_CT0` — 128-char CSRF token

### Extracting Tokens via agent-browser

If tokens are not set, connect to Chrome and extract them:

```bash
# 1. Launch Chrome with debugging (user must close Chrome first)
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-debug-profile &

# 2. Wait for Chrome to start, then navigate to x.com
sleep 5
agent-browser --cdp 9222 open "https://x.com"

# 3. Extract cookies (parse auth_token and ct0 from output)
agent-browser --cdp 9222 cookies get
```

Look for these lines in the cookies output:
```
auth_token=<40-char-hex>
ct0=<128-char-hex>
```

## Digest Generation Pipeline

### Step 1: Fetch Tweets

```bash
export X_AUTH_TOKEN="<token>"
export X_CT0="<token>"

cd /Users/dnazarov/code/twitter-monitor
python scripts/fetch_timeline.py --count 500 --output data/tweets_raw.json
```

- Fetches from the "For You" algorithmic feed (HomeTimeline GraphQL endpoint)
- 500 tweets typically covers 12-24 hours depending on activity
- Increase `--count` for longer time ranges

### Step 2: Filter by Time Window

```python
import json
from datetime import datetime, timedelta, timezone

with open("data/tweets_raw.json") as f:
    tweets = json.load(f)

now = datetime.now(timezone.utc)
cutoff = now - timedelta(hours=12)  # Adjust hours as needed

def parse_twitter_date(date_str):
    return datetime.strptime(date_str, "%a %b %d %H:%M:%S %z %Y")

filtered = [t for t in tweets
            if parse_twitter_date(t["created_at"]) >= cutoff]

with open("data/tweets_filtered.json", "w") as f:
    json.dump(filtered, f, indent=2)
```

### Step 2.5: Filter Promotional Content (Recommended)

```bash
python scripts/filter_promotional.py data/tweets_filtered.json \
  --output data/tweets_clean.json \
  --stats
```

**What it does:**
1. **Promotional detection** — removes CTA tweets ("Sign up", "Use code"), corporate self-promotion, giveaways, affiliate links
2. **News relevance scoring** — boosts tweets with breaking news language, factual reporting, corroboration by multiple accounts
3. **Composite ranking** — output sorted by news relevance (high) minus promo score (low)

**Key parameters:**
| Parameter | Default | Notes |
|-----------|---------|-------|
| `--promo-threshold` | 0.4 | Lower = stricter promo removal |
| `--min-news-score` | 0.0 | Higher = only keep newsworthy content |
| `--strict` | off | Sets promo=0.3, news=0.15 (stricter) |
| `--stats` | off | Print score distributions and top results |

Use `--strict` mode for a news-focused digest that aggressively removes promotional content.

### Step 3: Hybrid Clustering (Recommended)

Use the hybrid clustering script for best results:

```bash
python scripts/cluster_hybrid.py data/tweets_filtered.json \
  --output data/clusters/ \
  --min-cluster-size 5 \
  --min-samples 2 \
  --lambda-weight 0.35 \
  --umap-dims 10 \
  --cluster-method leaf
```

**How it works:**
1. **Preprocessing**: Strips URLs, normalizes @mentions to `@USER`, extracts hashtags/entities
2. **Dense embeddings**: `BAAI/bge-small-en-v1.5` (384-dim, good for short text)
3. **Sparse embeddings**: TF-IDF on cleaned text + entities (lexical anchoring)
4. **Hybrid vector**: `[dense ; λ * sparse]` — prevents "vibe clusters"
5. **UMAP reduction**: 1000+ dims → 10 dims for better clustering
6. **HDBSCAN**: Automatic cluster count, handles outliers
7. **Labeling**: c-TF-IDF + entity extraction + MMR diversity

**Key parameters:**
| Parameter | Default | Notes |
|-----------|---------|-------|
| `--min-cluster-size` | 5 | Smaller = more clusters |
| `--min-samples` | 2 | Smaller = less noise |
| `--lambda-weight` | 0.35 | Higher = more lexical influence |
| `--umap-dims` | 10 | Higher = preserves more structure |
| `--cluster-method` | leaf | `leaf` finds more clusters than `eom` |

**Quality gate**: Clustering passes if:
- `silhouette > 0.05`
- `noise_fraction < 35%`

**Output files:**
- `data/clusters/cluster_NN_label.txt` — Topic clusters for summarization
- `data/clusters/viral_highlights.txt` — High-engagement noise tweets (≥5k likes)
- `data/clusters/manifest.json` — Cluster stats, quality metrics, viral highlights data

### Step 4: Generate Digest

Read each cluster file in `data/clusters/*.txt` and synthesize into a markdown digest.

## Digest Format Requirements

**Every point must have a source citation** with a link to the original tweet:

```markdown
- @username said something interesting [(source)](https://x.com/username/status/tweet_id)
```

Tweet URLs follow the format: `https://x.com/{screen_name}/status/{id}`

### Digest Structure

```markdown
# Twitter Digest — Last N Hours
**X tweets | Y clusters | Z viral highlights**

---

## Topic Name

**Key narrative or theme:**
- Point with context [(source)](url)
- Another point [(source)](url)

---

## Viral Highlights

*High-engagement tweets that don't fit into topic clusters but clearly resonated:*

| Content | Likes | Source |
|---------|-------|--------|
| Description of tweet | 20.6k | [(source)](url) |

---

## Top Engagement

| Tweet | Likes | Link |
|-------|-------|------|
| Description | N | [(source)](url) |
```

### Viral Highlights

The clustering script automatically identifies high-engagement tweets (≥5k likes) that ended up in the noise bucket. These are typically:
- **Viral standalone content** — funny photos, observations that resonate
- **Sparse topics** — only 2-3 tweets about a subject (below cluster threshold)
- **Unique one-offs** — content with no similar peers

Include these in a **Viral Highlights** section. They may not fit a narrative but are clearly resonating with the audience.

### Output Location

Save digest to: `data/digest_YYYY-MM-DD.md`

## Step 5: Generate Audio Briefing (Optional)

Convert the markdown digest into a spoken-word audio file for passive consumption.

```bash
# Generate audio from digest
python scripts/generate_audio.py data/digest_2026-01-20.md --output data/briefing.mp3

# Choose voice and speed
python scripts/generate_audio.py digest.md --voice onyx --speed 1.1

# High-quality mode
python scripts/generate_audio.py digest.md --model tts-1-hd

# Script-only (no audio, just convert markdown to spoken text)
python scripts/generate_audio.py digest.md --script-only
```

**Requires:** `OPENAI_API_KEY` environment variable.

**Voices:** alloy, echo, fable, onyx, nova (default), shimmer

**Pipeline:** Markdown → spoken script (remove tables/links/formatting) → chunk (4000 chars) → OpenAI TTS → concatenate MP3

### Post with Audio

```bash
# Post digest text + audio file to Discord
python scripts/post_digest.py digest.md --discord-url https://... --audio data/briefing.mp3

# Auto-generate audio and post in one step
python scripts/post_digest.py digest.md --discord-url https://... --generate-audio
```

## Data Files

| File | Description |
|------|-------------|
| `data/tweets_raw.json` | Raw fetched tweets |
| `data/tweets_filtered.json` | Time-filtered tweets |
| `data/filtered.json` | After promotional filter (news-scored) |
| `data/clusters/*.txt` | Topic clusters for summarization |
| `data/clusters/viral_highlights.txt` | High-engagement unclustered tweets (≥5k likes) |
| `data/clusters/manifest.json` | Cluster statistics, quality metrics, viral highlights |
| `data/digest_*.md` | Generated digests |
| `data/briefing_*.mp3` | Audio briefings |
| `data/*.spoken.txt` | Spoken scripts (intermediate) |

## Scripts

| Script | Purpose | Notes |
|--------|---------|-------|
| `fetch_timeline.py` | Fetch tweets from X GraphQL API | |
| `extract_topics.py` | Extract topics, engagement tiers | |
| `filter_promotional.py` | Remove promo, score news relevance | **New** |
| `cluster_hybrid.py` | Dense+sparse+HDBSCAN clustering | **Recommended** |
| `cluster_embeddings.py` | Dense+K-means clustering | Fast, fixed k |
| `cluster_and_summarize.py` | Keyword regex clustering | Simple |
| `generate_digest.py` | Orchestrate full pipeline | |
| `build_simple_digest.py` | Build digest without LLM | |
| `generate_audio.py` | TTS audio briefing generation | **New** |
| `post_digest.py` | Post to Slack/Discord/webhook | Audio support added |

## Notes

- The HomeTimeline API returns the algorithmic "For You" feed
- Tokens expire periodically — re-extract if you get 401 errors
- Rate limit: ~0.3s delay between API requests (built into fetch script)
- 500 tweets ≈ 3-4 minutes to fetch
- Hybrid clustering takes ~10-15 seconds for 300 tweets
- Audio generation: ~1 min per 10,000 chars of digest (OpenAI TTS)
- Audio files are posted as Discord attachments (max 25 MB)
