#!/bin/bash
# ==========================================
# G4F 自动续期触发器 — 放服务器上每 30 分钟跑一次
# ==========================================

GITHUB_TOKEN="这里填你的PAT_TOKEN"
OWNER="c0yt"
REPO="g4f"
WORKFLOW="gaming4free.yml"

curl -s -X POST \
  -H "Authorization: Bearer ${GITHUB_TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/dispatches" \
  -d '{"ref":"main"}'

echo ""
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 触发请求已发送"
