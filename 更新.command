#!/bin/bash
# ダブルクリックで海外の数字を取り直し、動きがあればGitHubへ反映する。
cd "$(dirname "$0")" || exit 1
echo "=== アニメ海外人気ウォッチ 手動更新 ==="
python3 collect.py || { echo "取得に失敗しました"; read -r -p "Enterで閉じる"; exit 1; }
if git diff --quiet docs/data.json 2>/dev/null; then
  echo "先週から動きはありませんでした。"
else
  git add docs/data.json titles.json notes.json
  git commit -m "海外人気の手動更新 $(date '+%Y-%m-%d %H:%M')" && git push && echo "公開サイトに反映しました。"
fi
read -r -p "Enterで閉じる"
