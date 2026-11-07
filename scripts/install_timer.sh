#!/usr/bin/env bash
# 사용자 systemd 타이머 설치: 매일 16:10(KST)에 update_and_push.sh 실행
set -euo pipefail
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
sed "s#@PROJECT@#$PROJECT#g" "$PROJECT/deploy/systemd/food-price-update.service" > "$UNIT_DIR/food-price-update.service"
cp "$PROJECT/deploy/systemd/food-price-update.timer" "$UNIT_DIR/"
systemctl --user daemon-reload
systemctl --user enable --now food-price-update.timer
# 로그아웃 상태에서도 타이머가 돌도록
loginctl enable-linger "$USER" 2>/dev/null || echo "linger 설정 실패: sudo loginctl enable-linger $USER 를 실행하세요"
systemctl --user list-timers food-price-update.timer --no-pager
