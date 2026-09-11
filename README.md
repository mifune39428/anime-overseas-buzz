# アニメ海外人気ウォッチ

海外のアニメ視聴者がいまどの作品に票を入れているかを、**毎週の数字**で追いかける静的サイト。
GitHub Actions が1日2回走り、GitHub Pages が更新される。

```
r/anime 週間カルマ集計 ──┐
MyAnimeList(Jikan) ─────┼─> collect.py ──> docs/data.json ──> GitHub Pages
Anime Corner の見出し ──┘   整形・日本語化・推移の保持      1ファイルのサイトが読む
```

## 載せるもの・載せないもの

**海外の書き込みそのものは、取得も翻訳も掲載もしない。**
持つのは数字（順位・票数・コメント数・スコア）と、その数字から書き起こした独自の日本語の解説、
そして原文スレッドへのリンクだけ。実際の反応はリンク先で読んでもらう方針
（`akita_news_site` と同じ考え方）。

- 各話スレッドの **カルマ（賛成票）とコメント数**、前週からの増減と順位の動き
- **MyAnimeList のスコア・登録者数・日本語タイトル**
- 週ごとの短い**日本語の総括**（LLM に数字だけを渡して書かせる。翻訳ではない）
- 作品ごとの**票の推移**（折れ線とその週ごとの表）
- Anime Corner の**見出しとリンク**（本文は取らない。見出しは日本語の一行に直して併記）
- 最新の週の上位作品には、**海外のコメントの抜粋を日本語にしたもの**を数件（書き手の名前から原文へ飛べる）

サムネイルは MyAnimeList の画像URLをそのまま参照している（画像そのものはこのリポジトリに置いていない）。

## 数字の出どころ

| 何を | どこから | 備考 |
| --- | --- | --- |
| 週間カルマ・コメント数・順位 | [r/anime Karma Chart](https://github.com/abysswatcherbel/abysswatcherbel.github.io)（abysswatcherbel）の週次JSON | r/anime の各話スレッドを Reddit API で集計したもの。サイト下部で出典を明記している |
| スコア・登録者数・日本語タイトル | MyAnimeList（[Jikan API](https://jikan.moe/)）。応じないときは MAL の作品ページから日本語タイトルと登録者数だけを拾う | `titles.json` にキャッシュし、7日ごとに取り直す。どちらから取ったかは `src` に残る |
| 海外ニュースの見出し | Anime Corner の RSS | 見出しとリンクのみ |
| コメントの抜粋 | Reddit API（OAuth。`REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` を置いたときだけ動く） | 上位10作品×5件。原文は保存せず、日本語にしたものと書き手・票数・原文リンクだけ持つ |

Reddit 本体の `.json` は未認証だと弾かれる（403）ため、コメントを取るには OAuth が要る。
順位とカルマの集計は、上のチャートを使うので認証なしでも動く。
AniList の公開APIも 2026-09 現在は停止中（`The AniList API has been temporarily disabled`）なので使っていない。

## 週と日付

r/anime の集計は**金曜始まり**。週1は「その季節の最初の日以前で直近の金曜」から始まる
（2026年夏の第10週 = 8/28〜9/3 と一致することを確認した規則）。

## ファイル

| ファイル | 役割 |
| --- | --- |
| `collect.py` | 収集・日本語化・`docs/data.json` の書き出し |
| `llm_providers.py` | LLMの多段フォールバック（`akita_news_site` からのコピー） |
| `titles.json` | MAL の日本語タイトル・登録者数のキャッシュ |
| `notes.json` | 生成済みの週の総括と見出し日本語訳のキャッシュ（数字が変わった週だけ作り直す） |
| `reactions.json` | 取得済みのコメント抜粋（日本語）のキャッシュ。直近3週ぶんだけ残す |
| `docs/index.html` | サイト本体（依存なしの1ファイル、PWA対応） |
| `docs/data.json` | 生成データ。Actions が自動コミットする |
| `.github/workflows/update.yml` | 1日2回の自動実行 |
| `更新.command` | Mac から手動で即更新（ダブルクリック） |

## 使い方

```
python3 collect.py          # ふだんの更新（MALへの問い合わせは40作品まで）
python3 collect.py --full   # 日本語タイトルをまとめて取りに行く（初回や、季節が変わった直後）
```

MyAnimeList は Jikan 越しに 504 を返すことがよくある（2026-09 現在、キャッシュ済み以外はほぼ通らない）。
その場合は **MAL の作品ページから日本語タイトルと登録者数だけ**を拾う控えに切り替わる。
ページの文章は取らない。1秒に1件のペースで、取れた分は `titles.json` に残るので取り直しは起きない。
Jikan が25回続けて応じなければ、その回はAPIを諦めてページ側だけで拾う。
どちらも駄目なら英題のまま表示し、次の実行で優先的に拾い直す。

GitHub Actions 側（データセンターのIP）からページ取得が弾かれる可能性はある。
その場合でもサイトは英題で成立し、手元で `更新.command` を1度動かせばキャッシュが埋まる。

## 設定

### 海外のコメントを載せる（任意）

1. https://www.reddit.com/prefs/apps で「create another app...」を押す
2. 種別は **script** を選ぶ。name は何でもよい。redirect uri は `http://localhost:8080`（script では使われない）
3. 作成後、アプリ名の下に出る文字列が **client ID**、`secret` の欄が **client secret**
4. このフォルダの `.env` に置く（GitHub Actions で動かすなら Secrets にも同じ名前で入れる）

```
REDDIT_CLIENT_ID=（アプリ名の下の文字列）
REDDIT_CLIENT_SECRET=（secret の欄）
```

置かなければ、この機能だけが静かに省かれる（サイトの他の部分は変わらず動く）。
一度取ったスレッドは `reactions.json` に残り、取り直しはしない。

### そのほかの設定

- **LLM は任意**。キーが無くても数字・推移・リンクは全部出る（総括と見出しの日本語訳だけが省かれる）。
  使う場合は GitHub Secrets か、このフォルダの `.env` に `GEMINI_API_KEY` などを置く（gitignore 済み）。
- 公開するには GitHub の Pages 設定で「Deploy from a branch」→ `main` / `docs` を選ぶ。

## 調整しどころ（`collect.py` の定数）

- `KEEP_SEASONS = 2` — サイトに残す季節の数（今季と前季）
- `MAX_WEEKS = 14` — 1季節あたり探しに行く週の上限
- `JIKAN_MAX_PER_RUN = 40` — 1回の実行で MAL に問い合わせる作品数
- `REFRESH_DAYS = 7` — スコアと登録者数を取り直す間隔
- `NOTE_TOP_N = 10` — 週の総括を書かせるときに渡す上位作品数
- `NOTE_MAX_PER_RUN = 6` — 1回の実行で新しく書かせる総括の数（新しい週から順に埋まる）
- `JIKAN_GIVEUP_AFTER = 25` — Jikanを諦めてMALのページ側に切り替えるまでの連続失敗回数
- `PAGE_GIVEUP_AFTER = 5` — ページ取得も続けて失敗したとき、その回を打ち切る回数
- `NEWS_MAX = 12` — 載せる見出しの本数
- `REACTION_TOP_N = 10` / `REACTION_PER_THREAD = 5` — 反応を拾う作品数と、1スレッドあたりの件数
- `REACTION_KEEP_WEEKS = 3` — 反応を残す週数
