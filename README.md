# 눌림목 스캐너

우상향 추세 종목 중 이평선 지지 + 거래량 수축 눌림목을 찾아주는 웹 대시보드.
한국(KOSPI/KOSDAQ) + 미국 동시 지원.

## 탐지 조건
1. **우상향**: 종가 > 60일선, 20일선 > 60일선, 20일선 기울기 상승
2. **눌림 깊이**: 60일 고점 대비 3~18% 조정
3. **이평선 지지**: 10/20/60일선 중 하나에 3.5% 이내 근접
4. **거래량 수축**: 최근 3일 평균 < 20일 평균 × 0.85 (점수 반영)
5. **RSI**: 35~62 중립권
6. **보너스**: 캔들 변동폭 축소(VCP), 최근 고점 유지

점수(100점 만점)는 눌림 깊이 이상치(7.5%), 이평선 밀착도,
거래량 수축 정도, RSI 위치(45 부근)를 가중 합산.
조건 변경은 `scanner.py` 상단 `CONFIG`에서.

## Railway 배포 (얼마냐봇과 동일한 흐름)
1. 이 폴더를 GitHub 저장소에 푸시
2. Railway → New Project → Deploy from GitHub repo
3. 자동으로 Procfile 인식 → 배포 완료
4. Settings → Networking → Generate Domain으로 주소 생성

## 로컬 실행
```bash
pip install -r requirements.txt
uvicorn app:app --reload
# http://localhost:8000
```

## 종목 추가
`watchlist.txt`에 한 줄씩 추가 (한국 종목은 .KS/.KQ 접미사 필수)

## API
- `GET /api/scan?market=kr|us|all` — 스캔 결과 JSON (10분 캐시)
- `GET /api/scan?market=all&refresh=true` — 캐시 무시하고 재스캔

## 참고
- yfinance 기반이라 한국 종목은 일봉 기준 (15~20분 지연 시세)
- 눌림목 매매는 일봉으로 충분 — 장 마감 후 스캔이 가장 정확
- 첫 스캔은 200여 종목을 받아오느라 1~2분 걸림 (이후 캐시)
