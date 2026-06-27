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
     DISCORD_BRIDGE_URL=...    # Discord 投稿ブリッジの Webhook URL
     ```
     ※env は「その環境を編集できる人に見える」点に注意。
   - **`X_SEARCH_QUERY_ID` は env に設定しない**（空のままにする）。queryId は `FEATURES` と
     同一バンドルから採る必要があり、コード(`scripts/fetch_search.py`)が正本。env に古い値を入れると
     コードの正しい既定を上書きして `GRAPHQL_VALIDATION_FAILED` を起こす（実際それで詰まった）。
     X が rotation したら**コード側を PR で更新**する（下の「X スクレイパ保守」参照）。
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
   - fetch の stderr を必ず確認し、**原因で切り分ける**（0件＝即トークン失効と決めつけない）:
     - `HTTP 401` / `HTTP 403` → X_AUTH_TOKEN・X_CT0 失効。Discord に「⚠️ Token期限切れ（x.com の
       Cookie から auth_token/ct0 を再取得して Routine env を更新）」を POST して終了。
     - `HTTP 400` / `HTTP 422`（`GRAPHQL_VALIDATION_FAILED`）→ **トークンは無実**。X のスキーマ変更で
       `scripts/fetch_search.py` の `FEATURES`/`variables`/`X_SEARCH_QUERY_ID` が古い。Discord に
       「⚠️ X スクレイパが古い（features/queryId 要更新。Cookie 取り直しでは直らない）」を POST して終了。
     - ネットワーク例外・その他のエラー → Discord にエラー要旨を POST して終了。
     - エラー無しで data/tweets.json が **0件** → 単にヒット無し。Discord に「本日は対象ツイート 0 件」を
       POST して終了（警告ではない）。
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
fetch がエラー（401/403=トークン、400/422=スクレイパ定義が古い、その他）や 0 件のときは、上の手順2の
切り分けに従って**原因別の**メッセージを Discord に出して終了すること（ダイジェストは作らない）。
```

## GHA との関係

- `fetch-twitter-timeline.yml` の `schedule` cron は**停止済み**（コメントアウト）。GHA 実行分は消費しない。
- 手動の単発実行は引き続き GHA の `workflow_dispatch` で使える（Routine が長期間落ちた場合のフォールバック）。
  GHA に戻したいときは `schedule:` のコメントを外すだけ。
- トークン (`X_AUTH_TOKEN` / `X_CT0`) は GHA Secrets と Routine env で**別管理**。Routine 移行後は
  Routine env 側を最新に保つこと（GHA Secrets は手動フォールバック用に残してよい）。

## X スクレイパ保守（rotation 対応）

X は SearchTimeline の **queryId / features / variables を不定期に rotation** する。これがズレると
全クエリ `HTTP 400/422 GRAPHQL_VALIDATION_FAILED`（"Internal server error"）になる（**認証=401/403 とは別物**。
Cookie 取り直しでは直らない）。直し方：

1. ログイン状態の x.com で DevTools → Network → `SearchTimeline` を1件開く。
2. **3点を必ず同一リクエストから採る**（バラバラのバンドルから採ると噛み合わない）:
   - **queryId**: Request URL の `/i/api/graphql/<queryId>/SearchTimeline`
   - **features** / **variables**: Payload（または URL のクエリ文字列）
3. `scripts/fetch_search.py` の `SEARCH_QUERY_ID` 既定・`FEATURES`・`variables` を上記に差し替えて PR。
   `FEATURES` はキー集合・値とも完全一致が必要（過不足どちらでも 422）。
4. **queryId は env ではなくコードが正本**（`X_SEARCH_QUERY_ID` は空のまま）。features がコードにある以上、
   queryId も同じ場所で一緒に rotation させる方が事故らない。
