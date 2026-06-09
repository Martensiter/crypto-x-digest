# Installing Twitter Monitor

> **For the installing agent**: This file contains everything needed to set up this app. Follow steps in order. Use multiple choice questions for user input. Verify each major step before proceeding.

## Quick Summary

AI-powered Twitter timeline analysis tool. Fetches the user's X/Twitter home timeline via internal GraphQL API (no official API access needed), clusters tweets by topic using semantic embeddings, and generates narrative digests with cited sources. At the end of setup, you'll be able to fetch tweets and generate clustered topic files ready for LLM summarization.

## Prerequisites

### System Requirements

- **Python 3.10+** (tested with 3.14)
- **macOS, Linux, or Windows** with Python installed
- **Internet connection** to fetch tweets from X/Twitter

### External Services

| Service | Purpose | Required? |
|---------|---------|-----------|
| X/Twitter account | Source of auth tokens for API access | Yes |
| Claude Code or Claude API | Summarizing clustered tweets | For final digest only |

### Required Credentials

| Variable | Description | How to obtain |
|----------|-------------|---------------|
| `X_AUTH_TOKEN` | X/Twitter session token (40 char hex) | Extract from browser cookies |
| `X_CT0` | X/Twitter CSRF token (128 char hex) | Extract from browser cookies |

> **Note**: These tokens expire periodically. If the app gets 401 errors, re-extract tokens from the browser.

## Installation

### 1. Create Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### 2. Install Dependencies

```bash
pip install sentence-transformers hdbscan
```

This installs:
- `sentence-transformers` - For generating text embeddings
- `hdbscan` - For density-based clustering
- `numpy`, `scikit-learn` - Installed as dependencies

## Configuration

### Extracting Auth Tokens

> ⚠️ **VERY IMPORTANT**: This step is critical. The app cannot function without valid X/Twitter auth tokens. Follow these instructions carefully.

The installing agent should extract X/Twitter auth tokens using agent-browser with a fresh Chrome profile.

#### Option A: Using agent-browser with Fresh Chrome Profile (Recommended)

> **Why this approach?**
> - X/Twitter blocks "Chrome for Testing" (Playwright's default browser)
> - The user's main Chrome can't connect via debug mode without a custom data directory
> - Solution: Launch real Chrome with a fresh profile and remote debugging enabled

**Step 1: Launch Chrome with fresh profile and debugging**

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/twitter-auth-session \
  "https://x.com/login" > /dev/null 2>&1 &
```

**Step 2: Ask user to log in**

A real Chrome window will open with the X/Twitter login page. Ask the user to log in to their account there.

**Step 3: Extract cookies via agent-browser**

Once the user confirms they're logged in:

```bash
agent-browser --cdp 9222 cookies
```

**Step 4: Parse the output**

Look for these values in the cookies output:
- `auth_token` - 40 character hexadecimal string
- `ct0` - 128 character hexadecimal string

**Step 5: Close the temporary Chrome**

```bash
pkill -f "user-data-dir=/tmp/twitter-auth-session"
```

#### Option B: Manual Extraction (Fallback)

If agent-browser is not available, ask the user to:
1. Open x.com in their browser and ensure they're logged in
2. Open DevTools (F12) > Application > Cookies > `https://x.com`
3. Copy the values for:
   - `auth_token` - 40 character hexadecimal string (double-click to select)
   - `ct0` - 128 character hexadecimal string

See `docs/x-api-reverse-engineering.md` for detailed documentation.

### Setting Environment Variables

Once tokens are obtained, set them:

```bash
export X_AUTH_TOKEN="your_auth_token_here"
export X_CT0="your_ct0_token_here"
```

Or create a `.env` file (gitignored):
```
X_AUTH_TOKEN=your_auth_token_here
X_CT0=your_ct0_token_here
```

> **Security Note**: Never commit auth tokens to git. The `.gitignore` already excludes `.env` and `*.env` files.

## Running the App

### Full Pipeline (Recommended)

```bash
python scripts/generate_digest.py --fetch --count 1000
```

This will:
1. Fetch 1000 tweets from the user's timeline
2. Extract topics and deduplicate
3. Cluster tweets by topic
4. Output cluster files ready for summarization

### Step-by-Step Pipeline

#### Step 1: Fetch Tweets

```bash
python scripts/fetch_timeline.py --count 1000 --output data/tweets.json
```

Options:
- `--count N` - Number of tweets to fetch (default: 500)
- `--output FILE` - Output JSON file path
- `--delay SECONDS` - Delay between API requests (default: 0.3)

#### Step 2: Extract Topics

```bash
python scripts/extract_topics.py data/tweets.json --output data/enriched.json
```

Adds metadata: topics, hashtags, mentions, engagement tier, content type.

#### Step 3: Cluster by Topic (Keyword-based)

```bash
python scripts/cluster_and_summarize.py data/enriched.json --output data/clusters/
```

Groups tweets into predefined categories: ai, crypto, politics, tech, finance, culture, science, geopolitics, general.

#### Alternative: Semantic Clustering

For automatic topic discovery using embeddings:

```bash
python scripts/cluster_embeddings.py data/tweets.json --output data/clusters_semantic/
```

Options:
- `--algorithm kmeans|hdbscan` - Clustering algorithm (default: kmeans)
- `--n-clusters N` - Number of clusters for K-means (default: 20)
- `--min-cluster-size N` - Minimum cluster size for HDBSCAN (default: 15)
- `--model MODEL` - Sentence transformer model (default: all-MiniLM-L6-v2)

### Summarization

After clustering, summarize each cluster file using Claude:

**Option A: Claude Code**
- Read each cluster file in `data/clusters/`
- Ask Claude to summarize with narrative and source links

**Option B: Claude API**
- Call Claude API on each cluster file programmatically

## Output Files

| Path | Description |
|------|-------------|
| `data/tweets.json` | Raw fetched tweets |
| `data/enriched.json` | Tweets with extracted topics and metadata |
| `data/clusters/*.txt` | Topic clusters ready for LLM summarization |
| `data/clusters/manifest.json` | Cluster metadata and statistics |
| `data/clusters_semantic/*.txt` | Semantic clusters (if using embeddings) |

## Verification

After installation, verify everything works:

```bash
# 1. Check Python environment
source .venv/bin/activate
python -c "from sentence_transformers import SentenceTransformer; print('OK')"

# 2. Check auth tokens are set
python -c "import os; print('AUTH_TOKEN set:', bool(os.environ.get('X_AUTH_TOKEN'))); print('CT0 set:', bool(os.environ.get('X_CT0')))"

# 3. Test fetch (small count)
python scripts/fetch_timeline.py --count 20 --output data/test_tweets.json

# 4. Verify output
ls -la data/test_tweets.json
```

Expected results:
- [ ] `sentence_transformers` imports without error
- [ ] Both auth tokens are set
- [ ] Fetch completes without 401/403 errors
- [ ] `data/test_tweets.json` contains tweet data

## Troubleshooting

### 401 Unauthorized

Auth tokens have expired. Re-extract from browser cookies.

### 403 Forbidden

Account may be rate-limited or restricted. Wait and try again, or use a different account.

### Empty Response / No Tweets

- Check the user follows accounts that tweet actively
- Verify tokens are from a logged-in session
- Try increasing delay: `--delay 1.0`

### Import Errors

Ensure virtual environment is activated and dependencies installed:
```bash
source .venv/bin/activate
pip install sentence-transformers hdbscan
```

### Model Download Slow

First run downloads the embedding model (~90MB). This is normal.

## File Reference

Key files for understanding the codebase:

| File | Purpose |
|------|---------|
| `scripts/fetch_timeline.py` | Fetches tweets via X GraphQL API |
| `scripts/extract_topics.py` | Extracts topics using regex patterns |
| `scripts/cluster_embeddings.py` | Semantic clustering with embeddings |
| `scripts/cluster_and_summarize.py` | Topic-based clustering |
| `scripts/generate_digest.py` | Orchestrates the full pipeline |
| `docs/x-api-reverse-engineering.md` | API documentation and token extraction |

## Notes for Installing Agent

1. **Token extraction is critical**: The app cannot function without valid X auth tokens
2. **First model load is slow**: The embedding model downloads on first use
3. **Summarization is manual**: The pipeline prepares clusters but summarization requires Claude
4. **Tokens expire**: If any API call fails with 401, tokens need to be re-extracted
