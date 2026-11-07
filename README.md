# 오늘의 장바구니 시세

KAMIS(농산물유통정보, aT) Open API에서 채소·과일·축산·수산·식량·특용작물 244개 품목의
**소매/도매** 일별 시세를 받아 정적 사이트로 보여줍니다.

```
[내 PC: systemd 타이머 매일 09:10·16:10 KST]
   └─ scripts/update_and_push.sh
        ├─ scripts/fetch_prices.py → public/data/latest.json, history.json
        └─ git commit && git push
              ├─ GitHub Actions (CI: 테스트 + 데이터 검증)
              └─ Vercel Git 연동 → 자동 배포 → https://<project>.vercel.app
```

## 로컬 실행
```bash
python3 scripts/fetch_prices.py          # 데이터 수집 (표준 라이브러리만 사용)
python3 -m http.server -d public 8000    # http://localhost:8000
python3 -m unittest discover -s tests    # 테스트
```

## 배포 설정 (최초 1회)
1. GitHub에 빈 저장소 생성 후
   `git remote add origin git@github.com:<you>/food-price.git && git push -u origin main`
2. https://vercel.com/new → 저장소 Import → Framework `Other`, 설정은 `vercel.json`이 처리
3. `./scripts/install_timer.sh` → 매일 자동 갱신·푸시
4. (권장) KAMIS 개인 키 발급 후 `.env`에 입력 (`.env.example` 참고)

## 예측 방식
- 누적 이력 7일 이상: 최근 14일 선형회귀로 다음 조사일 가격 (±15% 제한)
- 그 전: 1개월 추세(일 환산) 60% + 전일 모멘텀 20% (±10% 제한)
- 참고용 단순 추정치입니다.
