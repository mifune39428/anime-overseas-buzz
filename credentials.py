"""メール送信の設定を既存のツールから借りてくる。

パスワードの置き場所を増やしたくないので、ニュース配信やブログ日次レポートが使っている
config_unified.json の email_settings をそのまま読む。場所は config.json で差し替えられる。
"""

from __future__ import annotations

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


class CredentialError(RuntimeError):
    """メール設定が見つからない。呼び出し側は送信を諦めてプレビューだけ残す。"""


def load_config(path: str | None = None) -> dict:
    with open(path or os.path.join(_HERE, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def load_email_settings(config_path: str) -> dict:
    if not os.path.exists(config_path):
        raise CredentialError(f"メール設定ファイルが見つかりません: {config_path}")
    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)
    settings = data.get("email_settings") or {}
    for key in ("sender_email", "sender_password", "receiver_email"):
        if not settings.get(key):
            raise CredentialError(f"email_settings.{key} が空です: {config_path}")
    return settings
