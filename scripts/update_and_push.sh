#!/usr/bin/env bash
# 시세를 수집하고 바뀐 데이터만 커밋·푸시한다. systemd 타이머가 매일 호출한다.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] && set -a && . ./.env && set +a   # KAMIS_CERT_KEY / KAMIS_CERT_ID

git pull --rebase --quiet || echo "git pull 실패 (원격 미설정?) - 계속 진행"
python3 scripts/fetch_prices.py

git add public/data
if git diff --cached --quiet; then
  echo "변경 없음"
  exit 0
fi
git commit --quiet -m "data: 시세 갱신 $(TZ=Asia/Seoul date +%F)"
if git remote get-url origin >/dev/null 2>&1; then
  git push --quiet origin HEAD
  echo "푸시 완료 → Vercel 자동 배포"
else
  echo "origin 원격이 없어 푸시는 건너뜀"
fi
