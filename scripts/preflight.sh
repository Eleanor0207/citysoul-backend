#!/usr/bin/env bash
# 開始下一張票之前先跑這支：看看 GitHub 上有什麼變了。
#
# 為什麼需要：4 個人各自用 AI 獨立開發、彼此不協調，所以「這張票是不是有人
# 已經做了」只能自己去看。#30 與 #32 就是在沒有這道檢查的情況下被做了兩次。
#
# ⚠️ **只看得到 push 上去的東西。** 同事在本機做到一半還沒 push 的分支，
# 這支腳本看不到——那是 4 人不協調的先天限制，不是漏掉哪個指令。檢查過了
# 也不代表沒人在做同一張票。
#
# 這支**只讀不寫**：fetch 只更新 origin/* 的指標，不動你的檔案、不動你的
# commit。不會 merge、不會 pull、不會 reset。
#
# 用法：
#   ./scripts/preflight.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== fetching =="
git fetch origin

echo
echo "== 他們有、你沒有的 commit（origin/main 領先的部分）=="
git log --oneline main..origin/main || true

echo
echo "== 你有、他們沒有的 commit（你本機領先的部分）=="
git log --oneline origin/main..main || true

echo
echo "== open issues =="
gh issue list --state open --limit 100 --json number --jq '[.[].number]|sort|join(", ")'

echo
echo "== closed issues =="
gh issue list --state closed --limit 100 --json number --jq '[.[].number]|sort|join(", ")'

echo
echo "挑一張「他們的 commit 裡沒出現過」的票，然後把這次看到的東西記進"
echo "docs/dev-notes/branch-tracker.md，Monday 比較時才有紀錄。"
