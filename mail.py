"""週のまとめをHTMLメールに組んで送る。

Gmailで崩れないよう、他のツール（amazon_wish_watch / blog_report_delivery）と
同じ作りにしてある: table と inline style だけ、CSSクラスなし。
"""

from __future__ import annotations

import re
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

BORDER = "#e3e6ea"
INK = "#1f2933"
MUTED = "#6b7684"
ACCENT = "#c2185b"
UP = "#0b7a3d"
DOWN = "#c0392b"

TOP_N = 10
VOICES_MAX = 6


def esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def jp_date(iso: str) -> str:
    m = re.match(r"\d{4}-(\d{2})-(\d{2})", iso or "")
    return f"{int(m.group(1))}月{int(m.group(2))}日" if m else ""


def post_id(url: str) -> str:
    m = re.search(r"/comments/([a-z0-9]+)", url or "")
    return m.group(1) if m else ""


def move_label(row: dict) -> str:
    if row.get("st") == "new":
        return f'<span style="color:{ACCENT};">NEW</span>'
    if row.get("st") == "returning":
        return f'<span style="color:{MUTED};">再登場</span>'
    dr = row.get("dr") or 0
    if dr > 0:
        return f'<span style="color:{UP};">▲{dr}</span>'
    if dr < 0:
        return f'<span style="color:{DOWN};">▼{abs(dr)}</span>'
    return f'<span style="color:{MUTED};">—</span>'


def _rank_rows(data: dict, week: dict) -> str:
    anime = data.get("anime", {})
    rows = sorted(week["rows"], key=lambda r: r["r"] or 999)[:TOP_N]
    out = []
    for r in rows:
        a = anime.get(str(r["id"])) or {}
        name = a.get("ja") or a.get("en") or a.get("romaji") or "?"
        ep = f'<span style="color:{MUTED};font-size:12px;"> 第{esc(r["ep"])}話</span>' if r.get("ep") else ""
        dk = r.get("dk") or 0
        dk_html = (
            f'<span style="color:{UP if dk > 0 else DOWN};font-size:12px;">'
            f'{"+" if dk > 0 else ""}{dk:,}</span>'
            if dk
            else ""
        )
        out.append(
            f'<tr>'
            f'<td style="padding:7px 6px;border-bottom:1px solid {BORDER};'
            f'font-size:15px;font-weight:bold;color:{INK};text-align:center;width:30px;">{r["r"]}</td>'
            f'<td style="padding:7px 6px;border-bottom:1px solid {BORDER};'
            f'font-size:13px;color:{INK};">{esc(name)}{ep}</td>'
            f'<td style="padding:7px 6px;border-bottom:1px solid {BORDER};'
            f'font-size:13px;color:{INK};text-align:right;white-space:nowrap;width:76px;">'
            f'{r["k"]:,}<span style="color:{MUTED};font-size:11px;">票</span>'
            f'{"<br>" + dk_html if dk_html else ""}</td>'
            f'<td style="padding:7px 6px;border-bottom:1px solid {BORDER};'
            f'font-size:12px;text-align:right;white-space:nowrap;width:44px;">{move_label(r)}</td>'
            f'</tr>'
        )
    return "".join(out)


def _voices(data: dict, week: dict) -> str:
    """その週の作品に付いた海外の反応（日本語にしたもの）を数件。"""
    reactions = data.get("reactions") or {}
    anime = data.get("anime", {})
    picked = []
    for r in sorted(week["rows"], key=lambda r: r["r"] or 999):
        entry = reactions.get(post_id(r.get("url") or ""))
        if not entry:
            continue
        a = anime.get(str(r["id"])) or {}
        name = a.get("ja") or a.get("en") or "?"
        for item in entry["items"][:2]:
            picked.append((name, item))
            if len(picked) >= VOICES_MAX:
                break
        if len(picked) >= VOICES_MAX:
            break
    if not picked:
        return ""

    blocks = []
    for name, item in picked:
        blocks.append(
            f'<tr><td style="padding:9px 11px;border:1px solid {BORDER};'
            f'border-radius:6px;background:#fafbfc;">'
            f'<div style="font-size:11px;color:{ACCENT};margin-bottom:3px;">{esc(name)}</div>'
            f'<div style="font-size:13px;color:{INK};line-height:1.7;">{esc(item["ja"])}</div>'
            f'<div style="font-size:11px;color:{MUTED};margin-top:4px;">'
            f'<a href="{esc(item["url"])}" style="color:{MUTED};">u/{esc(item["by"])}</a>'
            f'</div></td></tr><tr><td style="height:6px;"></td></tr>'
        )
    return (
        f'<div style="font-size:12px;color:{ACCENT};font-weight:bold;margin:22px 0 8px;">'
        f'海外の反応</div>'
        f'<div style="font-size:11px;color:{MUTED};margin-bottom:8px;">'
        f'掲示板の書き込みの抜粋を日本語にしたものです。名前から原文が開きます。</div>'
        f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%">'
        f'{"".join(blocks)}</table>'
    )


def build_html(data: dict, season: dict, week: dict, site_url: str) -> str:
    note = week.get("note") or ""
    note_html = (
        f'<div style="margin:20px 0 0;padding:12px 14px;background:#fdf2f6;'
        f'border-left:3px solid {ACCENT};">'
        f'<div style="font-size:11px;color:{ACCENT};font-weight:bold;margin-bottom:4px;">'
        f'この週の数字を読む</div>'
        f'<div style="font-size:13px;color:{INK};line-height:1.8;">{esc(note)}</div></div>'
        if note
        else ""
    )
    period = f'{jp_date(week.get("from", ""))}〜{jp_date(week.get("to", ""))}'

    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        f'<body style="margin:0;padding:0;background:#f4f5f7;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
        f'style="background:#f4f5f7;padding:18px 0;">'
        f'<tr><td align="center">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" width="600" '
        f'style="max-width:600px;width:100%;background:#ffffff;border:1px solid {BORDER};">'
        f'<tr><td style="padding:20px 22px;">'
        f'<div style="font-size:11px;color:{MUTED};letter-spacing:.08em;">アニメ海外人気ウォッチ</div>'
        f'<div style="font-size:19px;font-weight:bold;color:{INK};margin-top:4px;">'
        f'{esc(season["label"])} 第{week["w"]}週</div>'
        f'<div style="font-size:12px;color:{MUTED};margin-top:3px;">'
        f'{period}の放送・{len(week["rows"])}作品</div>'
        f'{note_html}'
        f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
        f'style="margin-top:18px;border-collapse:collapse;">{_rank_rows(data, week)}</table>'
        f'{_voices(data, week)}'
        f'<div style="margin-top:22px;text-align:center;">'
        f'<a href="{esc(site_url)}" style="display:inline-block;padding:10px 18px;'
        f'background:{ACCENT};color:#ffffff;text-decoration:none;font-size:13px;">'
        f'サイトで全部見る</a></div>'
        f'<div style="font-size:11px;color:{MUTED};margin-top:18px;line-height:1.7;">'
        f'票とコメント数は r/anime の各話スレッドの集計、スコアと日本語タイトルは MyAnimeList です。'
        f'数字は放送から48時間かけて積み上がるため、あとから順位が動くことがあります。</div>'
        f'</td></tr></table></td></tr></table></body></html>'
    )


def send_mail(subject: str, html_body: str, settings: dict, max_retries: int = 3) -> bool:
    sender = settings["sender_email"]
    password = settings["sender_password"]
    receiver = settings["receiver_email"]
    host = settings.get("smtp_server", "smtp.gmail.com")
    port = settings.get("smtp_port", 587)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr(("アニメ海外人気ウォッチ", sender))
    msg["To"] = receiver
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    for attempt in range(1, max_retries + 1):
        try:
            with smtplib.SMTP(host, port, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.login(sender, password)
                server.sendmail(sender, receiver, msg.as_string())
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"  メール送信に失敗 ({attempt}/{max_retries}): {exc}")
            if attempt < max_retries:
                time.sleep(5 * attempt)
    return False
