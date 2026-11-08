# 공룡삼촌의 장바구니 시세

KAMIS(농산물유통정보, aT) Open API(`dailyPriceByCategoryList`)에서 채소·과일·축산·수산·식량·특용작물의
**소매/도매·등급별** 일별 시세를 받아 정적 사이트로 보여줍니다. 응답의 당일·1일전·1주전·2주전 값에
실제 조사 날짜가 붙어 있어 날짜가 정확한 일별 이력(`history.json`)을 쌓습니다.

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
python3 scripts/fetch_prices.py --backfill 90   # 지난 90일 이력 채우기 (최초 1회, 약 5분)
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
- 누적 이력 7일 이상: 최근 14개 조사일 선형회귀로 다음 조사일 가격 (±15% 제한)
- 그 전: 1개월 추세(일 환산) (±10% 제한)
- 참고용 단순 추정치입니다.

## 아이콘 바꾸기
`public/favicon.svg` 한 파일만 교체하면 브라우저 탭 아이콘과 상단 로고가 함께 바뀝니다.

## 사이트의 "최신화" 버튼
버튼 → `api/refresh.js`(Vercel 서버 함수) → GitHub Actions `refresh.yml` 실행 → 수집·커밋·푸시 → Vercel 재배포 →
페이지가 새 `latest.json`을 감지해 다시 그림 (보통 1~2분). 남용을 막기 위해 10분 쿨다운이 있습니다.

설정 (최초 1회)
1. GitHub → Settings → Developer settings → Fine-grained tokens → Generate
   - Repository access: `food-price` 하나만, Permissions → Actions: **Read and write**
2. Vercel 프로젝트 → Settings → Environment Variables → `GITHUB_TOKEN` = 위 토큰 → Redeploy
3. (선택) 개인 KAMIS 키는 GitHub 레포 Settings → Secrets → Actions 에 `KAMIS_CERT_KEY`, `KAMIS_CERT_ID`
