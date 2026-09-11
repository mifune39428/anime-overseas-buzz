#!/usr/bin/env python3
"""海外のアニメ人気を「数字」で集めて docs/data.json に書き出す。

集めるもの:
  1. r/anime の週間カルマランキング（順位・カルマ・コメント数・話数・順位の増減）
  2. MyAnimeList のスコアと登録者数（Jikan API 経由）、および日本語タイトル
  3. Anime Corner の見出し（リンクのみ。本文は取得も掲載もしない）

掲示板のコメント本文は取得も翻訳も掲載もしない。数字と、数字から書き起こした
独自の日本語の解説、そして原文へのリンクだけを持たせる（akita_news_site と同じ方針）。

LLM は「あると良い」程度の扱いで、APIキーが無くてもサイトは数字だけで成立する。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

OUTPUT_PATH = os.path.join(BASE_DIR, "docs", "data.json")
TITLES_PATH = os.path.join(BASE_DIR, "titles.json")
NOTES_PATH = os.path.join(BASE_DIR, "notes.json")

USER_AGENT = "anime-overseas-buzz/1.0 (+https://github.com/mifune39428)"
FETCH_TIMEOUT = 25

# r/anime の週間カルマチャート（Reddit の公開APIから作られている集計データ）。
# Reddit 本体の .json は未認証だと弾かれるようになったため、集計済みのこちらを使う。
CHART_BASE = (
    "https://raw.githubusercontent.com/abysswatcherbel/"
    "abysswatcherbel.github.io/HEAD/docs/static/data"
)
CHART_CREDIT = {
    "name": "r/anime Karma Chart",
    "author": "abysswatcherbel",
    "url": "https://github.com/abysswatcherbel/abysswatcherbel.github.io",
}

JIKAN_ANIME = "https://api.jikan.moe/v4/anime/{mal_id}"
ANIME_CORNER_FEED = "https://animecorner.me/feed/"

SEASON_ORDER = ["winter", "spring", "summer", "fall"]
SEASON_JA = {"winter": "冬", "spring": "春", "summer": "夏", "fall": "秋"}
SEASON_FIRST_MONTH = {"winter": 1, "spring": 4, "summer": 7, "fall": 10}

# サイトに載せる季節の数（今季と前季）。
KEEP_SEASONS = 2
# 1季節あたり探しに行く週の上限。
MAX_WEEKS = 14
# 1回の実行で Jikan に問い合わせる作品数の上限（無料APIへの負荷を抑えるため）。
JIKAN_MAX_PER_RUN = 40
# Jikan の制限は毎秒3回・毎分60回。余裕を持って1秒に1回にする。
JIKAN_INTERVAL = 1.1
# MyAnimeList 側が落ちていると Jikan は 504 を返し続ける。
# 504 は「MAL側に取りに行けなかった」という意味で、粘っても同じ回であることが多い。
# そのIDは早々に諦めて次へ進み、連続でこの回数しくじったらその回ごと打ち切る。
JIKAN_GIVEUP_AFTER = 25
# スコアと登録者数を取り直す間隔。
REFRESH_DAYS = 7
# 週の総括を書かせる対象（上位何作品の数字を渡すか）。
NOTE_TOP_N = 10
# 1回の実行で新しく書かせる総括の数。新しい週から順に埋め、残りは次の実行に回す
# （初回に無料枠を使い切らないための蓋）。
NOTE_MAX_PER_RUN = 6
# 見出しを日本語にする本数の上限。
NEWS_MAX = 12


# ---------------------------------------------------------------- 小道具

def load_env() -> None:
    """.env があれば読む（GitHub Actions では Secrets が環境変数で入るので不要）。"""
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def jst_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))


def season_of(date: dt.date) -> tuple[int, str]:
    return date.year, SEASON_ORDER[(date.month - 1) // 3]


def prev_season(year: int, season: str) -> tuple[int, str]:
    i = SEASON_ORDER.index(season)
    return (year - 1, "fall") if i == 0 else (year, SEASON_ORDER[i - 1])


def week_start(year: int, season: str, week: int) -> dt.date:
    """その週の開始日（金曜）。

    週1は「季節の最初の日以前で直近の金曜」から始まる。
    2026年夏の週10が 8/28〜9/3 であることと一致する規則。
    """
    first = dt.date(year, SEASON_FIRST_MONTH[season], 1)
    anchor = first - dt.timedelta(days=(first.weekday() - 4) % 7)
    return anchor + dt.timedelta(days=7 * (week - 1))


def season_label(year: int, season: str) -> str:
    return f"{year}年{SEASON_JA[season]}アニメ"


def fetch(
    url: str, timeout: int = FETCH_TIMEOUT, tries: int = 3, backoff: int = 3
) -> bytes | None:
    """取れなければ None を返す（1本落ちてもサイト全体は作れるようにする）。

    MyAnimeList は Jikan 越しに 504 を返すことが珍しくないので、
    待ち時間を伸ばしながら何度か試す。それでも駄目な分は次の実行に回る。
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(tries):
        last = attempt == tries - 1
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return res.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503, 504) and not last:
                time.sleep(backoff * (attempt + 1))
                continue
            print(f"  ! {e.code} {url}", file=sys.stderr)
            return None
        except Exception as e:  # noqa: BLE001  ネットワーク周りは種類を問わず諦める
            if not last:
                time.sleep(backoff * (attempt + 1))
                continue
            print(f"  ! {type(e).__name__} {url}", file=sys.stderr)
            return None
    return None


def fetch_json(url: str, tries: int = 3, backoff: int = 3) -> object | None:
    raw = fetch(url, tries=tries, backoff=backoff)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"  ! JSONとして読めません {url}", file=sys.stderr)
        return None


def load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: str, data, compact: bool = False) -> None:
    """キャッシュは差分を読めるように整形し、サイトが読むデータは詰めて書く。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if compact:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        else:
            json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


# ---------------------------------------------------------------- カルマチャート

def load_season(year: int, season: str) -> tuple[list[dict], dict[str, dict]]:
    """その季節の週データを、若い週から順に集める。

    戻り値は (週ごとの順位表, 作品の基本情報)。基本情報は新しい週の内容で上書きする。
    """
    weeks: list[dict] = []
    infos: dict[str, dict] = {}
    misses = 0
    for week in range(1, MAX_WEEKS + 1):
        url = f"{CHART_BASE}/{year}/{season}/week_{week}.json"
        rows = fetch_json(url)
        if not rows:
            misses += 1
            if misses >= 2:  # 連続で無ければその季節は終わり
                break
            continue
        misses = 0
        if not isinstance(rows, list):
            continue
        for raw in rows:
            if raw.get("mal_id"):
                infos[str(raw["mal_id"])] = anime_info(raw)
        start = week_start(year, season, week)
        weeks.append(
            {
                "w": week,
                "from": start.isoformat(),
                "to": (start + dt.timedelta(days=6)).isoformat(),
                "rows": [compact_row(r) for r in rows if r.get("mal_id")],
            }
        )
        print(f"  週{week}: {len(rows)}作品")
    return weeks, infos


def compact_row(r: dict) -> dict:
    """1作品ぶんの週データを、サイトが使う形に絞る。

    rank_change は整数のほかに "new"（初登場）と "returning"（再登場）が入ってくる。
    順位の増減（dr）と状態（st）に分けて持たせる。
    """
    change = r.get("rank_change")
    return {
        "id": int(r["mal_id"]),
        "r": r.get("current_rank"),
        "dr": change if isinstance(change, int) else 0,
        "st": change if isinstance(change, str) else "",
        "k": r.get("karma") or 0,
        "dk": r.get("karma_change") or 0,
        "c": r.get("comments") or 0,
        "ep": str(r.get("episode") or ""),
        "url": r.get("url") or "",
    }


def anime_info(r: dict) -> dict:
    """チャート側が持っている作品の基本情報（日本語タイトル以外）。"""
    streams = []
    for s in r.get("streams") or []:
        name = (s or {}).get("name")
        if name:
            streams.append([name, (s or {}).get("url") or ""])
    images = r.get("images") or {}
    return {
        "en": r.get("title_english") or r.get("title") or "",
        "romaji": r.get("title") or "",
        "img": images.get("medium") or images.get("large") or "",
        "studio": [s for s in (r.get("studio") or []) if s],
        "streams": streams,
        "score": r.get("score"),
        "eps": r.get("num_episodes") or 0,
    }


# ---------------------------------------------------------------- 日本語タイトル

def jikan_budget() -> int:
    """1回の実行で MAL に問い合わせる作品数。`--full` で上限を外す（初回の取り込み用）。"""
    if "--full" in sys.argv:
        return 10_000
    return JIKAN_MAX_PER_RUN


def enrich_titles(anime: dict, cache: dict) -> None:
    """日本語タイトルと登録者数を Jikan から補う。取れた分はキャッシュに残す。"""
    today = jst_now().date().isoformat()
    stale = (jst_now().date() - dt.timedelta(days=REFRESH_DAYS)).isoformat()

    todo = []
    for mal_id in anime:
        c = cache.get(mal_id)
        if not c or not c.get("ja"):
            todo.append((0, mal_id))          # 日本語タイトルが無いものが最優先
        elif c.get("fetched", "") < stale:
            todo.append((1, mal_id))          # 数字の取り直し
    todo.sort()
    todo = todo[:jikan_budget()]
    if todo:
        print(f"MyAnimeList から {len(todo)} 作品を取得")

    misses = 0
    for i, (_, mal_id) in enumerate(todo):
        if i:
            time.sleep(JIKAN_INTERVAL)
        data = fetch_json(JIKAN_ANIME.format(mal_id=mal_id), tries=2, backoff=1)
        d = (data or {}).get("data") if isinstance(data, dict) else None
        if not d:
            misses += 1
            if misses >= JIKAN_GIVEUP_AFTER:
                print(
                    f"  MyAnimeList に{misses}回続けて繋がらないので、この回は打ち切ります",
                    file=sys.stderr,
                )
                break
            continue
        misses = 0
        cache[mal_id] = {
            "ja": d.get("title_japanese") or "",
            "score": d.get("score"),
            "members": d.get("members"),
            "mal": d.get("url") or f"https://myanimelist.net/anime/{mal_id}",
            "fetched": today,
        }

    got = sum(1 for _, m in todo if cache.get(m, {}).get("fetched") == today)
    if todo:
        print(f"  取得できたのは {got}/{len(todo)} 作品（残りは次の実行で拾い直す）")

    for mal_id, info in anime.items():
        c = cache.get(mal_id) or {}
        info["ja"] = c.get("ja") or ""
        info["members"] = c.get("members")
        info["mal"] = c.get("mal") or f"https://myanimelist.net/anime/{mal_id}"
        if c.get("score"):  # MAL の最新スコアのほうが新しい
            info["score"] = c["score"]


# ---------------------------------------------------------------- 海外の見出し

def strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def fetch_news() -> list[dict]:
    """Anime Corner の見出しとリンクだけを取る（本文は取らない）。"""
    raw = fetch(ANIME_CORNER_FEED)
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    items = []
    for item in root.iterfind(".//item"):
        title = strip_tags(item.findtext("title") or "")
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        items.append(
            {
                "en": title,
                "url": link,
                "date": parse_rss_date(item.findtext("pubDate") or ""),
                "source": "Anime Corner",
            }
        )
    return items[:NEWS_MAX]


def parse_rss_date(value: str) -> str:
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            return dt.datetime.strptime(value.strip(), fmt).astimezone(
                dt.timezone(dt.timedelta(hours=9))
            ).date().isoformat()
        except ValueError:
            continue
    return ""


# ---------------------------------------------------------------- 日本語の解説

LLM_KEYS = ("GEMINI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")


def llm():
    """キーが1つも無ければ LLM は使わない（数字だけでサイトは成立する）。"""
    if not any(os.environ.get(k) for k in LLM_KEYS):
        return None
    try:
        import llm_providers  # noqa: PLC0415  キー未設定なら使わないので遅延読み込み
        return llm_providers
    except Exception:  # noqa: BLE001
        return None


# この実行で実際に使った notes.json のキー。使わなくなった分は最後に捨てる。
USED_NOTES: set[str] = set()


def digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def week_note(season_key: str, week: dict, anime: dict, notes: dict, budget: list[int]) -> str:
    """その週の数字から、日本語の短い総括を書かせる。翻訳ではなく独自の記述。"""
    rows = sorted(week["rows"], key=lambda r: r["r"] or 999)[:NOTE_TOP_N]
    if not rows:
        return ""
    lines = []
    for r in rows:
        a = anime.get(str(r["id"])) or {}
        name = a.get("ja") or a.get("en") or a.get("romaji") or "?"
        if r.get("st") == "new":
            move = "今週が初登場"
        elif r.get("st") == "returning":
            move = "圏外から再登場"
        elif r["dr"]:
            move = f"前週から{abs(r['dr'])}つ{'上昇' if r['dr'] > 0 else '下降'}"
        else:
            move = "前週と同順位"
        lines.append(
            f"{r['r']}位 {name}（第{r['ep']}話）カルマ{r['k']}"
            f"（前週差{r['dk']:+d}）コメント{r['c']}件 {move}"
        )
    facts = "\n".join(lines)
    key = f"{season_key}-w{week['w']}-{digest(facts)}"
    USED_NOTES.add(key)
    if key in notes:
        return notes[key]

    if budget[0] <= 0:
        return ""
    provider = llm()
    if provider is None:
        return ""
    prompt = (
        "あなたは日本のアニメブログの書き手です。以下は、海外の掲示板 r/anime の"
        "各話感想スレッドに付いた「カルマ（賛成票）」と「コメント数」の、ある1週間の集計です。\n\n"
        f"{facts}\n\n"
        "この数字だけを根拠に、日本語で3〜4文の短い総括を書いてください。条件:\n"
        "・海外の視聴者の書き込みを訳したり、引用したりしない（数字の読み解きに徹する）\n"
        "・順位の入れ替わり、カルマの伸び、コメント数と票数の比（議論の起きやすさ）に触れる\n"
        "・断定しすぎず、「〜が効いたとみられる」程度にとどめる\n"
        "・見出しや箇条書きにせず、本文だけを出力する\n"
    )
    try:
        text = provider.generate_text(prompt).strip()
    except Exception as e:  # noqa: BLE001  LLMが落ちても数字だけで公開する
        print(f"  ! 総括の生成に失敗: {type(e).__name__}", file=sys.stderr)
        return ""
    text = re.sub(r"\s*\n\s*", "", text)
    budget[0] -= 1
    notes[key] = text
    return text


def news_gists(items: list[dict], notes: dict) -> None:
    """英語の見出しを、日本語の一行の要旨に置き換える（本文は使わない）。"""
    pending = [i for i in items if f"news-{digest(i['url'])}" not in notes]
    for i in items:
        USED_NOTES.add(f"news-{digest(i['url'])}")
        cached = notes.get(f"news-{digest(i['url'])}")
        if cached:
            i["ja"] = cached
    if not pending:
        return
    provider = llm()
    if provider is None:
        return
    listing = "\n".join(f"{n + 1}. {i['en']}" for n, i in enumerate(pending))
    prompt = (
        "次は海外のアニメニュースサイトの見出しです。それぞれを、日本語の一行"
        "（40字以内・事実のみ・体言止め可）に直してください。\n"
        "作品名は日本での正式名称が分かればそれを使い、分からなければ英語のままで構いません。\n"
        "番号と本文だけを、入力と同じ順番・同じ行数で出力してください。\n\n"
        f"{listing}\n"
    )
    try:
        text = provider.generate_text(prompt)
    except Exception as e:  # noqa: BLE001
        print(f"  ! 見出しの日本語化に失敗: {type(e).__name__}", file=sys.stderr)
        return
    lines = [re.sub(r"^\s*\d+[.)、]\s*", "", ln).strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) != len(pending):
        print("  ! 見出しの行数が合わないので英語のまま載せます", file=sys.stderr)
        return
    for item, ja in zip(pending, lines):
        item["ja"] = ja
        notes[f"news-{digest(item['url'])}"] = ja


# ---------------------------------------------------------------- 組み立て

def build() -> None:
    today = jst_now().date()
    year, season = season_of(today)
    targets = [(year, season)]
    for _ in range(KEEP_SEASONS - 1):
        targets.append(prev_season(*targets[-1]))

    titles_cache = load_json(TITLES_PATH, {})
    notes = load_json(NOTES_PATH, {})
    anime: dict[str, dict] = {}
    seasons: list[dict] = []

    for y, s in targets:
        print(f"{season_label(y, s)} を取得中…")
        weeks, infos = load_season(y, s)
        if not weeks:
            print("  （データがまだありません）")
            continue
        anime.update(infos)
        seasons.append(
            {
                "key": f"{y}-{s}",
                "label": season_label(y, s),
                "year": y,
                "season": s,
                "weeks": weeks,
            }
        )

    if not seasons:
        print("チャートを取得できませんでした。既存の data.json は残します。", file=sys.stderr)
        sys.exit(1)

    enrich_titles(anime, titles_cache)

    # 新しい週から順に総括を書く（途中で上限に達したら、残りは次の実行で埋まる）
    budget = [NOTE_MAX_PER_RUN]
    pairs = [(sea, w) for sea in seasons for w in sea["weeks"]]
    pairs.sort(key=lambda pw: (pw[1]["from"]), reverse=True)
    for sea, week in pairs:
        week["note"] = week_note(sea["key"], week, anime, notes, budget)

    news = fetch_news()
    news_gists(news, notes)

    save_json(TITLES_PATH, titles_cache)
    save_json(NOTES_PATH, {k: v for k, v in notes.items() if k in USED_NOTES})
    save_json(
        OUTPUT_PATH,
        {
            "generated_at": jst_now().isoformat(timespec="minutes"),
            "credit": CHART_CREDIT,
            "anime": anime,
            "seasons": seasons,
            "news": news,
        },
        compact=True,
    )
    total = sum(len(s["weeks"]) for s in seasons)
    print(f"書き出しました: {OUTPUT_PATH}（{len(seasons)}季節 / {total}週 / {len(anime)}作品）")


if __name__ == "__main__":
    load_env()
    build()
