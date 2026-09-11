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

import base64
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
REACTIONS_PATH = os.path.join(BASE_DIR, "reactions.json")

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
# Jikan が 504 を返し続けるとき用の控え。MAL の作品ページから日本語タイトルだけ拾う。
MAL_PAGE = "https://myanimelist.net/anime/{mal_id}"
ANIME_CORNER_FEED = "https://animecorner.me/feed/"

# r/anime のコメント。未認証では取れないので、Reddit の「script」アプリを登録して
# REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET を .env に置いたときだけ動く。
# 認証なしで読める RSS。票数は入らないが、登録の手間なしで反応を拾える（既定の経路）。
REDDIT_RSS = "https://www.reddit.com/r/anime/comments/{post_id}.rss"
# 認証情報を置いた場合だけ使う経路。票数が取れて、票の多い順に並べられる。
REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_COMMENTS = "https://oauth.reddit.com/comments/{post_id}"

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
# 控えのページ取得も、連続でこの回数しくじったらその回は打ち切る。
PAGE_GIVEUP_AFTER = 5
# スコアと登録者数を取り直す間隔。
REFRESH_DAYS = 7
# 週の総括を書かせる対象（上位何作品の数字を渡すか）。
NOTE_TOP_N = 10
# 1回の実行で新しく書かせる総括の数。新しい週から順に埋め、残りは次の実行に回す
# （初回に無料枠を使い切らないための蓋）。
NOTE_MAX_PER_RUN = 6
# 見出しを日本語にする本数の上限。
NEWS_MAX = 12

# --- 海外の反応（コメントの抜粋）---
# 最新の週の上位何作品ぶん、スレッドを見に行くか。
REACTION_TOP_N = 10
# 1スレッドから拾うコメントの数。
REACTION_PER_THREAD = 5
# LLM に渡す前に、1コメントをこの文字数で切る（全文は取り込まない）。
REACTION_EXCERPT = 600
# 何週ぶんの反応を残すか（古い週のものは落とす）。
REACTION_KEEP_WEEKS = 3
# RSS は連続で叩くとすぐ429を返す。実測では数十秒空ければ通る。
REACTION_INTERVAL = 30
# 1回の実行で新しく取りに行くスレッド数。残りは次の実行に回る。
REACTION_MAX_PER_RUN = 5


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


def mal_page_lookup(mal_id: str) -> dict | None:
    """MAL の作品ページから日本語タイトルと登録者数だけを取り出す。

    Jikan（MAL公式の無料API）が 504 を返し続けるときの控え。
    載せるのは事実の項目だけで、ページの文章は取らない。
    """
    raw = fetch(MAL_PAGE.format(mal_id=mal_id), tries=2, backoff=2)
    if not raw:
        return None
    page = raw.decode("utf-8", "replace")

    def field(label: str) -> str:
        m = re.search(label + r":</span>(.*?)</div>", page, re.S)
        return strip_tags(m.group(1)) if m else ""

    ja = field("Japanese")
    if not ja:
        return None
    members = re.search(r"Members:</span>\s*([\d,]+)", page)
    return {
        "ja": ja,
        "members": int(members.group(1).replace(",", "")) if members else None,
        "mal": MAL_PAGE.format(mal_id=mal_id),
    }


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

    use_jikan = True      # 504 が続いたら、その回はAPIを諦めてページ側だけで拾う
    jikan_misses = 0
    page_misses = 0
    for i, (_, mal_id) in enumerate(todo):
        if i:
            time.sleep(JIKAN_INTERVAL)
        record = None

        if use_jikan:
            data = fetch_json(JIKAN_ANIME.format(mal_id=mal_id), tries=2, backoff=1)
            d = (data or {}).get("data") if isinstance(data, dict) else None
            if d:
                jikan_misses = 0
                record = {
                    "ja": d.get("title_japanese") or "",
                    "score": d.get("score"),
                    "members": d.get("members"),
                    "mal": d.get("url") or MAL_PAGE.format(mal_id=mal_id),
                    "src": "jikan",
                }
            else:
                jikan_misses += 1
                if jikan_misses >= JIKAN_GIVEUP_AFTER:
                    use_jikan = False
                    print(
                        f"  Jikan が{jikan_misses}回続けて応じないので、"
                        "この回はMALのページから拾います",
                        file=sys.stderr,
                    )

        if record is None or not record.get("ja"):
            page = mal_page_lookup(mal_id)
            if page:
                page_misses = 0
                record = {**page, "score": (record or {}).get("score"), "src": "page"}
            else:
                page_misses += 1
                if page_misses >= PAGE_GIVEUP_AFTER:
                    print(
                        f"  MyAnimeList に{page_misses}回続けて繋がらないので、"
                        "この回は打ち切ります",
                        file=sys.stderr,
                    )
                    break
                if record is None:
                    continue

        cache[mal_id] = {**record, "fetched": today}

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


# ---------------------------------------------------------------- 海外の反応

REDDIT_KEYS = ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET")


def reddit_token() -> str | None:
    """アプリ単体（client_credentials）でアクセストークンを取る。

    Reddit は未認証だと 403 を返すようになったので、コメントを見るには
    「script」種別のアプリを登録して、その ID と secret を .env に置く必要がある。
    置いていなければ、この機能ごと静かに省かれる。
    """
    cid = os.environ.get("REDDIT_CLIENT_ID")
    secret = os.environ.get("REDDIT_CLIENT_SECRET")
    if not cid or not secret:
        return None
    body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    req = urllib.request.Request(
        REDDIT_TOKEN_URL,
        data=body,
        headers={
            "Authorization": f"Basic {auth}",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as res:
            return json.load(res).get("access_token")
    except Exception as e:  # noqa: BLE001
        print(f"  ! Reddit の認証に失敗: {type(e).__name__}", file=sys.stderr)
        return None


def post_id(url: str) -> str:
    """スレッドのURLから投稿IDを取り出す。"""
    m = re.search(r"/comments/([a-z0-9]+)", url or "")
    return m.group(1) if m else ""


def clean_comment(text: str) -> str:
    """引用・リンク・装飾を落として、地の文だけを残す。"""
    text = html.unescape(text or "")
    text = re.sub(r"(?m)^\s*>.*$", " ", text)                          # 引用行
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)             # リンク
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[*_~`#^]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_comments(token: str, url: str) -> list[dict]:
    """1スレッドから、票の多いコメントを数件ぶん取る。

    取るのは「誰が・何票・どこに」と、日本語にするための短い抜粋だけ。
    原文はこのリポジトリに保存しない。
    """
    pid = post_id(url)
    if not pid:
        return []
    query = urllib.parse.urlencode(
        {"sort": "top", "limit": 25, "depth": 1, "raw_json": 1}
    )
    req = urllib.request.Request(
        f"{REDDIT_COMMENTS.format(post_id=pid)}?{query}",
        headers={"Authorization": f"bearer {token}", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as res:
            payload = json.load(res)
    except Exception as e:  # noqa: BLE001
        print(f"  ! コメントを取れません（{pid}）: {type(e).__name__}", file=sys.stderr)
        return []

    try:
        children = payload[1]["data"]["children"]
    except (IndexError, KeyError, TypeError):
        return []

    out = []
    for child in children:
        if child.get("kind") != "t1":
            continue
        d = child.get("data") or {}
        author = d.get("author") or ""
        body = clean_comment(d.get("body") or "")
        if author in ("AutoModerator", "[deleted]") or not author:
            continue
        if body in ("[deleted]", "[removed]") or len(body) < 40:
            continue
        out.append(
            {
                "by": author,
                "score": d.get("score") or 0,
                "url": "https://www.reddit.com" + (d.get("permalink") or ""),
                "excerpt": body[:REACTION_EXCERPT],
            }
        )
        if len(out) >= 12:      # 候補として多めに返し、選別は japanese_voices に任せる
            break
    return out


BOT_AUTHORS = {"AutoLovepon", "AnimeMod", "AutoModerator", "[deleted]", ""}


def fetch_comments_rss(url: str) -> list[dict]:
    """認証なしで、スレッドのコメントを RSS から拾う。

    Reddit の RSS には票数が入らないので「票の多い順」には並べられない。
    代わりに、そのスレッドに付いた書き込みから、地の文のあるものを拾って
    後段の LLM に選ばせる。取れるのは書き手・原文リンク・抜粋だけ。
    """
    pid = post_id(url)
    if not pid:
        return []
    raw = fetch(f"{REDDIT_RSS.format(post_id=pid)}?limit=50", tries=1)
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []

    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for entry in root.findall("a:entry", ns):
        author = (entry.findtext("a:author/a:name", default="", namespaces=ns) or "").removeprefix("/u/")
        if author in BOT_AUTHORS:
            continue
        link = entry.find("a:link", ns)
        body = clean_comment(
            re.sub(r"<[^>]+>", " ", entry.findtext("a:content", default="", namespaces=ns) or "")
        )
        if len(body) < 60:
            continue
        out.append(
            {
                "by": author,
                "url": (link.get("href") if link is not None else url),
                "excerpt": body[:REACTION_EXCERPT],
            }
        )
    return out


def japanese_voices(items: list[dict], title: str, want: int) -> list[dict]:
    """候補のコメントから代表的なものを選び、日本語の1〜2文にして返す。

    RSS 経路には票数が無いので、どれが読まれた書き込みかを機械的に決められない。
    そこで候補をまとめて渡し、反応として分かりやすいものを選ばせる。
    選別に失敗したときは、先頭から順に訳す形へ落とす。
    """
    provider = llm()
    if provider is None or not items:
        return []
    pool = items[:12]
    listing = "\n\n".join(f"{n + 1}. {i['excerpt']}" for n, i in enumerate(pool))
    prompt = (
        f"海外のアニメ掲示板で、アニメ『{title}』の最新話について書かれた感想です。\n"
        f"この中から、反応として分かりやすいものを{min(want, len(pool))}件選び、\n"
        "それぞれ日本語の1〜2文（80字程度）にしてください。条件:\n"
        "・逐語訳ではなく、何を面白がっているか・何に驚いたかが伝わる要約にする\n"
        "・強いネタバレは避け、反応の温度が分かる程度にとどめる\n"
        "・あいさつだけ、絵文字だけ、他の人への短い相づちのようなものは選ばない\n"
        "・出力は1行につき「元の番号|日本語」の形だけ。説明や見出しは書かない\n\n"
        f"{listing}\n"
    )
    try:
        text = provider.generate_text(prompt)
    except Exception as e:  # noqa: BLE001
        print(f"  ! 反応の日本語化に失敗: {type(e).__name__}", file=sys.stderr)
        return []

    out = []
    used = set()
    for line in text.splitlines():
        m = re.match(r"\s*(\d+)\s*[|｜]\s*(.+)", line.strip())
        if not m:
            continue
        idx = int(m.group(1)) - 1
        ja = m.group(2).strip()
        if idx in used or not (0 <= idx < len(pool)) or len(ja) < 10:
            continue
        used.add(idx)
        out.append({**{k: v for k, v in pool[idx].items() if k != "excerpt"}, "ja": ja})
        if len(out) >= want:
            break
    if not out:
        print("  ! 反応を選べなかったので、この回は載せません", file=sys.stderr)
    return out


def collect_reactions(seasons: list[dict], anime: dict, cache: dict) -> dict:
    """最新の週の上位作品について、海外のコメントの抜粋を日本語で用意する。

    既定は認証の要らない RSS。`REDDIT_CLIENT_ID` を置いてあれば、票数が取れて
    票の多い順に並べられる API 経路を使う。
    """
    token = reddit_token()
    latest = seasons[0]["weeks"][-1]
    rows = sorted(latest["rows"], key=lambda r: r["r"] or 999)[:REACTION_TOP_N]

    fresh: dict[str, dict] = {}
    fetched = 0
    misses = 0
    for r in rows:
        pid = post_id(r.get("url") or "")
        if not pid:
            continue
        if pid in cache:                       # 一度取ったスレッドは取り直さない
            fresh[pid] = cache[pid]
            continue
        if fetched >= REACTION_MAX_PER_RUN:    # 残りは次の実行で
            continue

        if fetched:
            time.sleep(1.2 if token else REACTION_INTERVAL)
        candidates = fetch_comments(token, r["url"]) if token else fetch_comments_rss(r["url"])
        fetched += 1
        if not candidates:
            misses += 1
            if misses >= 2:
                print(
                    "  Reddit が続けて応じないので、この回は打ち切ります"
                    "（RSSの回数制限のことが多い）",
                    file=sys.stderr,
                )
                break
            continue
        misses = 0

        a = anime.get(str(r["id"])) or {}
        name = a.get("ja") or a.get("en") or a.get("romaji") or ""
        items = japanese_voices(candidates, name, REACTION_PER_THREAD)
        if items:
            fresh[pid] = {
                "url": r["url"],
                "items": items,
                "fetched": jst_now().date().isoformat(),
            }
            print(f"  反応: {name} 第{r['ep']}話 {len(items)}件")

    # 古い週のぶんは落とす
    keep = set()
    for sea in seasons:
        for w in sea["weeks"][-REACTION_KEEP_WEEKS:]:
            for row in w["rows"]:
                keep.add(post_id(row.get("url") or ""))
    return {k: v for k, v in {**cache, **fresh}.items() if k in keep}


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
    for i, (sea, week) in enumerate(pairs):
        week["note"] = week_note(sea["key"], week, anime, notes, budget)
        # 最新の週はメールの本文にもなるので、しくじったらこの回のうちにもう一度試す
        # （古い週に生成枠を食われて、いつまでも空のままになるのを防ぐ）
        if i == 0 and not week["note"]:
            week["note"] = week_note(sea["key"], week, anime, notes, budget)

    news = fetch_news()
    news_gists(news, notes)

    reactions = collect_reactions(seasons, anime, load_json(REACTIONS_PATH, {}))

    save_json(TITLES_PATH, titles_cache)
    save_json(REACTIONS_PATH, reactions)
    save_json(NOTES_PATH, {k: v for k, v in notes.items() if k in USED_NOTES})
    save_json(
        OUTPUT_PATH,
        {
            "generated_at": jst_now().isoformat(timespec="minutes"),
            "credit": CHART_CREDIT,
            "anime": anime,
            "seasons": seasons,
            "news": news,
            "reactions": reactions,
        },
        compact=True,
    )
    total = sum(len(s["weeks"]) for s in seasons)
    voices = sum(len(v["items"]) for v in reactions.values())
    print(
        f"書き出しました: {OUTPUT_PATH}"
        f"（{len(seasons)}季節 / {total}週 / {len(anime)}作品 / 反応{voices}件）"
    )


if __name__ == "__main__":
    load_env()
    build()
