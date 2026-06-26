# Crypto X Digest を Claude Code Routine で回す

GitHub Actions の cron (`fetch-twitter-timeline.yml`) に代わり、**Claude Code on the web
の Routine**（スケジュール実行のエージェントセッション）で毎日のダイジェストを生成・投稿する構成。

- GHA の実行分は消費せず、**あなたの Claude (Max) 利用枠**を消費する（Routine には1日あたりの
  実行回数上限あり）。
- 取得スクリプトはそのまま流用。日本語化はエージェント (Claude) 自身が行うため、Routine 環境に
  `ANTHROPIC_API_KEY` を置く必要はない（`scripts/translate_digest.py` を使わず native に翻訳）。
- docs: <https://code.claude.com/docs/en/routines>

## 仕組み（GHA fetch-twitter-timeline.yml と同等のパイプライン）

```
Routine（毎日, 依存はエージェントが手順1で導入）
  1. pip install pyyaml ; git pull origin <作業ブランチ>
  2. scripts/fetch_search.py で信頼ソース from: + キーワード検索を取得 → data/tweets.json
     0件 / トークン失効なら Discord に警告して終了（X_AUTH_TOKEN / X_CT0 の再取得を促す）
  3. extract_topics.py → filter_promotional.py → cluster_and_summarize.py → build_simple_digest.py
     で英語ダイジェスト data/digest.md を生成（GHA と同じ順序・同じ引数）
  4. エージェント自身が data/digest.md を日本語へ翻訳し data/digest.ja.md に書く
     （API キー不要。翻訳失敗時は英語版でフォールバック）
  5. DISCORD_BRIDGE_URL へ 3500 字チャンクで投稿
  6. 結果を1行サマリで報告
```

## セットアップ（claude.ai 側・あなたの操作）

1. **環境を作成** (<https://claude.ai/code/routines> → Environments)
   - Environment variables（`.env` 形式）:
     ```
     X_AUTH_TOKEN=...          # 40 桁セッショントークン（x.com の auth_token Cookie）
     X_CT0=...                 # 128 桁 CSRF トークン（ct0 Cookie）
     X_SEARCH_QUERY_ID=...     # GraphQL SearchTimeline の queryId
     DISCORD_BRIDGE_URL=...    # Discord 投稿ブリッジの Webhook URL
     ```
     ※env は「その環境を編集できる人に見える」点に注意。
   - **Network access = Custom**、Allowed domains に `x.com` / `api.x.com` / `twitter.com` と
     Discord ブリッジのホストを追加し、「include default list」も有効化（`api.anthropic.com` は既定で許可）。
2. **Routine を作成**
   - Repository: `Martensiter/crypto-x-digest`
   - Model: Sonnet（翻訳＋スクリプト実行が主なので Sonnet で十分。コスト優先）
   - Schedule: daily（cron 例 `0 0 * * *` = 9:00 JST）
   - **Setup script**: 空欄でOK（依存はエージェントが手順1で導入）。
   - Prompt: 下記をそのまま貼り付け。
3. 初回は手動 Fire して、Discord に投稿が出るか・トークンが生きているかを確認。

## Routine プロンプト（貼り付け用）

```
あなたは暗号資産の「業界動向」を毎日まとめる担当です。X(Twitter) から関連投稿を取得し、
日本語ダイジェストを作って Discord に投稿します。進捗と結果は日本語で報告してください。

手順:
1. `pip install pyyaml` を実行し、`git pull origin <このリポジトリの作業ブランチ>` で最新化する。
2. 取得: `mkdir -p data && python3 scripts/fetch_search.py --queries-file config/search_queries.yaml
   --accounts-file config/source_accounts.yaml --max-age-hours 48 --output data/tweets.json`
   - data/tweets.json が無い / 0件 / fetch がエラー（401 等）なら、X_AUTH_TOKEN・X_CT0 失効の
     可能性が高い。DISCORD_BRIDGE_URL に「⚠️ Token期限切れ通知（x.com の Cookie から再取得して
     Routine env を更新してください）」を POST し、ここで終了する。
3. 整形パイプライン（GHA と同じ）:
   a. `python3 scripts/extract_topics.py data/tweets.json --output data/enriched.json --force-topic crypto`
   b. `python3 scripts/filter_promotional.py data/enriched.json --output data/filtered.json --stats`
   c. `mkdir -p data/clusters && python3 scripts/cluster_and_summarize.py data/filtered.json --output data/clusters`
   d. `python3 scripts/build_simple_digest.py data/filtered.json --accounts-file config/source_accounts.yaml
      --output data/digest.md --title "Crypto Digest (X Search)"`
4. data/digest.md を Read し、**あなた自身が**自然な日本語へ翻訳して data/digest.ja.md に Write する
   （見出し・箇条書き・(source) リンクの構造とツイートURLは保持。固有名詞・ティッカーはそのまま）。
   翻訳が困難な場合は英語版(data/digest.md)をそのまま投稿に使ってよい。
5. Discord へ投稿する。投稿先は data/digest.ja.md（無ければ data/digest.md）。本文を 3500 字以下の
   チャンクに分割し、各チャンクを `{"title": "暗号通貨ダイジェスト（X検索）(i/n)", "content": "..."}`
   の JSON にして `curl -sS -X POST -H "Content-Type: application/json" -d @<chunk> "$DISCORD_BRIDGE_URL"`
   で順に送る（チャンク間 1.5 秒スリープ）。
6. 「取得件数 / クラスタ数 / 投稿チャンク数 / 翻訳の成否」を1行で報告して終了する。

注意: data/ への変更はコミット不要（ダイジェストは Discord 投稿が本番。リポジトリには残さなくてよい）。
トークン失効や 0 件のときは無理に投稿せず、警告を出して終了すること。
```

## GHA との関係

- `fetch-twitter-timeline.yml` の `schedule` cron は**停止済み**（コメントアウト）。GHA 実行分は消費しない。
- 手動の単発実行は引き続き GHA の `workflow_dispatch` で使える（Routine が長期間落ちた場合のフォールバック）。
  GHA に戻したいときは `schedule:` のコメントを外すだけ。
- トークン (`X_AUTH_TOKEN` / `X_CT0`) は GHA Secrets と Routine env で**別管理**。Routine 移行後は
  Routine env 側を最新に保つこと（GHA Secrets は手動フォールバック用に残してよい）。
