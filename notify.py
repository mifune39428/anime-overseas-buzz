#!/usr/bin/env python3
"""新しい週がサイトに載ったら、その週のまとめをメールで知らせる。

公開中の data.json を見て、最新の週が前回知らせたものと違えば1通送る。
週が変わっていなければ何もしない（他のツールと同じく、変化があったときだけ知らせる）。

数字は48時間かけて積み上がるので、週が現れた直後はまだ数作品しか載っていない。
その状態で送っても順位表にならないため、前の週に対して十分な数が揃うまで待つ。

launchd から1日に何度か呼ばれる前提。送るのは週に1回程度になる。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import credentials  # noqa: E402
import mail  # noqa: E402

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "notified.json")

USER_AGENT = "anime-overseas-buzz/1.0 (+https://github.com/mifune39428)"
FETCH_TIMEOUT = 30

# 新しい週を「順位表として読める」と見なす条件。
# 前の週の作品数に対する割合と、最低限の作品数。
MIN_RATIO = 0.6
MIN_ENTRIES = 20


def load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")


def fetch_live(url: str) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as res:
            return json.load(res)
    except Exception as e:  # noqa: BLE001
        print(f"公開中のデータを読めません: {type(e).__name__}")
        return None


def main() -> int:
    config = load_json(CONFIG_PATH, {})
    data = fetch_live(config.get("data_url") or "")
    if not data or not data.get("seasons"):
        return 1

    season = data["seasons"][0]
    weeks = season.get("weeks") or []
    if not weeks:
        return 1
    week = weeks[-1]
    key = f"{season['key']}-w{week['w']}"

    state = load_json(STATE_PATH, {})
    if state.get("key") == key:
        print(f"新しい週はまだありません（{season['label']} 第{week['w']}週のまま）")
        return 0

    # 数字が出揃うまで待つ
    prev = len(weeks[-2]["rows"]) if len(weeks) > 1 else 0
    need = max(MIN_ENTRIES, int(prev * MIN_RATIO))
    if len(week["rows"]) < need:
        print(
            f"第{week['w']}週は集計中です（{len(week['rows'])}作品／{need}作品そろってから知らせます）"
        )
        return 0

    try:
        settings = credentials.load_email_settings(config["email"]["config_path"])
    except (credentials.CredentialError, KeyError) as e:
        print(f"メール設定を読めません: {e}")
        return 1

    subject = f"【海外人気】{season['label']} 第{week['w']}週"
    html = mail.build_html(data, season, week, config.get("site_url", ""))
    if not mail.send_mail(subject, html, settings):
        return 1

    save_json(STATE_PATH, {"key": key, "sent_at": data.get("generated_at", "")})
    print(f"送りました: {subject}（{len(week['rows'])}作品）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
