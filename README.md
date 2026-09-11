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

サムネイルは MyAnimeList の画像URLをそのまま参照している（画像そのものはこのリポジトリに置いていない）。

## 数字の出どころ

| 何を | どこから | 備考 |
| --- | --- | --- |
| 週間カルマ・コメント数・順位 | [r/anime Karma Chart](https://github.com/abysswatcherbel/abysswatcherbel.github.io)（abysswatcherbel）の週次JSON | r/anime の各話スレッドを Reddit API で集計したもの。サイト下部で出典を明記している |
| スコア・登録者数・日本語タイトル | MyAnimeList（[Jikan API](https://jikan.moe/)） | `titles.json` にキャッシュし、7日ごとに取り直す |
| 海外ニュースの見出し | Anime Corner の RSS | 見出しとリンクのみ |

Reddit 本体の `.json` は未認証だと弾かれるようになったため、直接は叩いていない。
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
| `docs/index.html` | サイト本体（依存なしの1ファイル、PWA対応） |
| `docs/data.json` | 生成データ。Actions が自動コミットする |
| `.github/workflows/update.yml` | 1日2回の自動実行 |
| `更新.command` | Mac から手動で即更新（ダブルクリック） |

## 使い方

```
python3 collect.py          # ふだんの更新（MALへの問い合わせは40作品まで）
python3 collect.py --full   # 日本語タイトルをまとめて取りに行く（初回や、季節が変わった直後）
```

MyAnimeList は Jikan 越しに 504 を返すことがよくある。取れなかった作品は英題のまま表示され、
次の実行で優先的に取り直される（`titles.json` に溜まっていく）。

## 設定

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
- `JIKAN_GIVEUP_AFTER = 8` — MALに連続で繋がらなかったとき、その回を打ち切る回数
- `NEWS_MAX = 12` — 載せる見出しの本数
