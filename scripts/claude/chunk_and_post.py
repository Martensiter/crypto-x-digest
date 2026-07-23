import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
MAX = 1900

lines = text.split("\n")
chunks = []
current = ""
for line in lines:
    candidate = current + ("\n" if current else "") + line
    if len(candidate) > MAX and current:
        chunks.append(current)
        current = line
    else:
        current = candidate
if current:
    chunks.append(current)

out_dir = Path("data/chunks")
out_dir.mkdir(parents=True, exist_ok=True)
n = len(chunks)
for i, chunk in enumerate(chunks, 1):
    payload = {
        "title": f"暗号通貨ダイジェスト（X検索）({i}/{n})",
        "content": chunk,
    }
    chunk_path = out_dir / f"chunk_{i:02d}.json"
    chunk_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

print(n)
