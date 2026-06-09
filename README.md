# Twitter Monitor

> [reflective-technologies/twitter-monitor](https://github.com/reflective-technologies/twitter-monitor) の MIT フォーク。crypto 業界動向向けに改変。

AI-powered Twitter timeline analysis with semantic clustering. Fetches your home timeline via X's internal API, clusters tweets by topic using embeddings, and generates narrative digests with cited sources.

## Features

- **Fetch tweets** via X's internal GraphQL API (no official API access needed)
- **Promotional content filter** — removes corporate promos, CTAs, and self-promotion; scores news relevance
- **Semantic clustering** using sentence-transformers embeddings + K-means
- **Parallel summarization** with Claude for efficient processing
- **Verified citations** — every claim links to the source tweet
- **Audio briefing** — TTS generation for passive consumption (listen while commuting)

## Quick Start

```bash
# 1. Install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install sentence-transformers hdbscan

# 2. Set auth tokens (see docs/x-api-reverse-engineering.md)
export X_AUTH_TOKEN="your_auth_token"
export X_CT0="your_ct0_token"

# 3. Fetch tweets
python scripts/fetch_timeline.py --count 1000 --output data/tweets.json

# 4. Cluster with embeddings
python scripts/cluster_embeddings.py data/tweets.json --output data/clusters/

# 5. Summarize clusters (via Claude Code or API)
```

## Architecture

```
Raw tweets (5000)
    │
    ▼
Deduplicate → 2287 unique
    │
    ▼
Filter promotional content & score news relevance  ← NEW
    │
    ▼
Embed with all-MiniLM-L6-v2 (local, ~3 sec)
    │
    ▼
K-means clustering → 20 topic clusters
    │
    ▼
Parallel LLM summarization (one per cluster)
    │
    ▼
Merge into final digest with verified citations
    │
    ▼
(Optional) Generate TTS audio briefing  ← NEW
    │
    ▼
Post to Discord (text + audio attachment)
```

### Why Semantic Clustering?

| Approach | Problem |
|----------|---------|
| Arbitrary chunks | Splits related tweets across chunks |
| Keyword matching | Misses semantic similarity ("Claude" vs "Anthropic's model") |
| **Embeddings** | Groups by meaning, finds natural topics |

## Scripts

| Script | Purpose |
|--------|---------|
| `fetch_timeline.py` | Fetch tweets from X's GraphQL API |
| `extract_topics.py` | Lightweight topic extraction via regex |
| `filter_promotional.py` | Remove promotional content, score news relevance |
| `cluster_embeddings.py` | Semantic clustering with embeddings |
| `cluster_and_summarize.py` | Keyword-based clustering (simpler) |
| `generate_digest.py` | Orchestrates the full pipeline |
| `generate_audio.py` | Generate TTS audio briefing from digest |
| `post_digest.py` | Post digest to Slack, Discord, or custom webhook |

## Getting Auth Tokens

1. Open x.com and log in
2. DevTools → Application → Cookies → `https://x.com`
3. Copy `auth_token` and `ct0` values
4. Set as environment variables

See [docs/x-api-reverse-engineering.md](docs/x-api-reverse-engineering.md) for details.

## Output

The pipeline generates:
- `data/tweets.json` — Raw fetched tweets
- `data/clusters/*.txt` — Topic clusters ready for summarization
- `twitter-digest-v2.md` — Final narrative digest with citations

Example citation format:
```markdown
Trump has been posting AI-generated images of himself taking over Greenland
[source](https://x.com/harryjsisson/status/2013502243362803807)
```

## Sample Digest

See [twitter-digest-v2.md](twitter-digest-v2.md) for a full example analyzing 2,046 tweets across 20 semantic clusters.

## Filtering Promotional Content

The pipeline includes a promotional content filter that removes corporate self-promotion and scores tweets for news relevance.

```bash
# Basic filtering (removes obvious promo, keeps everything else)
python scripts/filter_promotional.py data/tweets.json --output data/clean.json

# Strict mode (aggressive promo removal + minimum news score required)
python scripts/filter_promotional.py data/tweets.json --output data/clean.json --strict

# View score statistics
python scripts/filter_promotional.py data/tweets.json --output data/clean.json --stats
```

**Promotional signals detected:**
- CTAs: "Sign up", "Use code", "Link in bio", "Shop now"
- Giveaway / contest language
- Affiliate / tracking URLs
- Excessive hashtags or emojis
- Thread-bait patterns

**News relevance signals:**
- Breaking news / timeliness keywords
- Factual language (reported, announced, confirmed)
- Corroboration (entity discussed by multiple accounts)
- High engagement-to-follower ratio
- Verified / high-follower accounts

## Audio Briefing (TTS)

Generate a spoken-word audio briefing from any digest for passive consumption.

```bash
# Generate audio (requires OPENAI_API_KEY)
python scripts/generate_audio.py digest.md --output briefing.mp3

# Choose voice and speed
python scripts/generate_audio.py digest.md --voice onyx --speed 1.1 --model tts-1-hd

# Post digest + audio to Discord in one step
python scripts/post_digest.py digest.md --discord-url https://... --generate-audio
```

**Available voices:** alloy, echo, fable, onyx, nova (default), shimmer

The audio pipeline converts markdown to a natural spoken script (removing tables, links, formatting), then generates audio via OpenAI TTS API.

## Customizing Post Destinations

ダイジェストの投稿先を Slack、Discord、カスタム Webhook などにカスタマイズできます。

### 1. 設定ファイル

```bash
cp config/post_destinations.example.yaml config/post_destinations.yaml
```

`config/post_destinations.yaml` を編集して、使用する投稿先を有効化:

- **Slack**: Incoming Webhook URL を設定
- **Discord**: Webhook URL を設定
- **Webhook**: 任意の HTTP エンドポイントへ JSON で POST

### 2. 環境変数（推奨）

シークレットは環境変数で指定することを推奨:

```bash
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
```

設定ファイルでは `"${SLACK_WEBHOOK_URL}"` のように参照できます。

### 3. 依存関係（設定ファイル使用時）

`config/post_destinations.yaml` を使う場合は PyYAML が必要:

```bash
pip install pyyaml
```

### 4. 投稿の実行

```bash
# 設定ファイルを使う
python scripts/post_digest.py digest.md --config config/post_destinations.yaml

# CLI で直接指定
python scripts/post_digest.py digest.md --slack-url "https://hooks.slack.com/..."
python scripts/post_digest.py twitter-digest-v2.md --discord-url "https://discord.com/api/webhooks/..."
```

### フォーク時のカスタマイズ

1. GitHub で [リポジトリをフォーク](https://github.com/reflective-technologies/twitter-monitor/fork)
2. フォークをクローン: `git clone https://github.com/YOUR_USER/twitter-monitor`
3. `config/post_destinations.yaml` を作成し、投稿先を設定
4. `.gitignore` に `config/post_destinations.yaml` を追加（シークレット保護）

## License

MIT
