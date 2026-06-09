# Twitter Digest Architecture

## Old Approach (Chunk & Merge)

```
5000 tweets
    │
    ▼
Split into 10 arbitrary chunks (500 each)
    │
    ▼
10 parallel LLM calls (summarize each chunk)
    │
    ▼
Merge summaries into final digest
```

**Problems:**
- Arbitrary chunking breaks topic threads
- Same story split across multiple chunks
- Duplicate analysis of same narratives
- No prioritization (noise = signal)
- High token usage (~500 tweets × 10 = 5000 tweets processed)

---

## New Approach (Topic Clustering)

```
5000 tweets
    │
    ▼
Deduplicate → 2287 unique tweets
    │
    ▼
Extract metadata (no LLM, fast)
  - Topics (keyword patterns)
  - Engagement tier
  - Content type
    │
    ▼
Cluster by topic
  - AI: 269 tweets
  - Politics: 72 tweets
  - Crypto: 58 tweets
  - etc.
    │
    ▼
Prioritize within clusters
  - All viral tweets
  - All high engagement
  - Sample medium/low for diversity
  - Max 100 per cluster
    │
    ▼
9 parallel LLM calls (one per topic)
    │
    ▼
Merge topic summaries into cohesive digest
```

**Benefits:**
- Related tweets stay together
- Each cluster is semantically coherent
- Prioritization focuses on high-signal content
- Fewer tokens (~600 tweets vs 5000)
- Better narrative flow (stories don't get split)

---

## Token Efficiency Comparison

| Approach | Tweets Processed | Est. Input Tokens | Quality |
|----------|-----------------|-------------------|---------|
| Old (chunk) | 5,033 | ~750K | Medium - fragmented |
| New (cluster) | ~600 | ~90K | High - coherent topics |

**~8x more efficient** with better output quality.

---

## Pipeline Scripts

```
scripts/
├── fetch_timeline.py      # Fetch from X API
├── extract_topics.py      # Stage 1: Metadata extraction
├── cluster_and_summarize.py  # Stage 2: Clustering
└── generate_digest.py     # Orchestrator
```

### Usage

```bash
# Set auth
export X_AUTH_TOKEN="..."
export X_CT0="..."

# Run full pipeline
python scripts/generate_digest.py --fetch --count 5000

# Or with existing data
python scripts/generate_digest.py --input data/tweets.json
```

### Output

```
data/
├── timeline_YYYYMMDD_HHMMSS.json  # Raw tweets
├── enriched.json                   # With extracted metadata
└── clusters/
    ├── manifest.json               # Cluster metadata
    ├── ai.txt                      # AI cluster (ready for LLM)
    ├── politics.txt
    ├── crypto.txt
    └── ...
```

---

## Future Improvements

### 1. Embeddings for Better Clustering
Replace keyword matching with semantic embeddings:
```python
from sentence_transformers import SentenceTransformer
model = SentenceTransformer('all-MiniLM-L6-v2')
embeddings = model.encode([t['text'] for t in tweets])
# Use HDBSCAN for clustering
```

### 2. Thread Detection
Group replies into conversation threads before clustering.

### 3. Trend Detection
Compare current clusters to historical data to highlight emerging topics.

### 4. Query-Driven Summaries
Instead of summarizing everything, answer specific questions:
- "What's the AI discourse today?"
- "Any breaking news?"
- "What's going viral?"

### 5. Incremental Updates
Only process new tweets since last run, merge with cached summaries.
