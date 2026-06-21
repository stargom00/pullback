"""
눌림목 스캐너 — 웹 서버
모드: pullback(눌림목) / turnaround(추세전환) / leader / super / breakout / surge
RS 모멘텀: 3개월 수익률 백분위 - 12개월 수익률 백분위 (시장별)
실행: uvicorn app:app --host 0.0.0.0 --port 8000

[변경 이력]
v4.21.0 밸류에이션 배지(ROE·PER밴드) — 참고용/온디맨드. fundamentals.py +
        /api/fundamentals/{ticker}. 카드 '밸류 보기' 클릭 시만 호출.
        진입조건엔 미반영. 미국주 yfinance(PER밴드), 한국주 네이버(PER/PBR/ROE).
v4.20.0 (1) R 손익비를 실제 진입가 기준으로 수정 (2) 상단 지수 바 추가.
        [문제1] 돌파/박스돌파 카드의 손익비 R이 '피벗 진입' 가정이라,
                이미 피벗 위로 연장된 자리에서 사면 1R(=pivot-stop)이 실제보다
                작아 손익비가 부풀려짐. 리스크%는 현재가 기준인데 R은 피벗 기준
                → 한 카드 안에서 분모가 두 개.
        [해결1] rr_info(entry=) 인자 추가. 이미 돌파한 돌파/박스돌파는
                entry=close(현재가)로 통일. 눌림목/돌파임박은 피벗 진입이 맞아
                기존대로 pivot 폴백 유지. 박스돌파 risk_pct도 현재가 기준으로.
                [검증] MS 예: 옛 1R=$6.67(3%)→ 새 1R=$9.33(4.2%), 실전 2R 정직.
        [추가2] /api/indices: 코스피·코스닥(네이버 fetch_index) + 나스닥(yf ^IXIC).
                상단 지수 바, 60초 캐시/갱신. 한국식 색(상승 빨강/하락 파랑).
v4.19.0 U/D Volume(매집/분산 비율) 추가 — 오닐 지표.
        [추가] up_down_volume(): 최근 50일 상승일거래량÷하락일거래량.
               1.0↑ 매집(기관 매수), 1.0↓ 분산(기관 매도).
               돌파/박스돌파/돌파임박 카드 RSI 옆에 "U/D X.X" 표시.
               1.0 이상 초록, 미만 주황. 거래량 폭발해도 분산이면 주의.
v4.18.1 돌파임박 손절을 '의미있는 지지(폭락바닥 제외)'로.
        [문제] 한올바이오파마 손절 38,400 = 리스크 30.94%(비현실적).
               38,400은 5/20 폭락 바닥 한 번. 손절을 단순 최저가(min)로
               잡아서 폭락 꼬리 하나에 손절이 끌려감.
               (20일선 -2%는 현재가 위라 후보에서 빠지고 폭락바닥만 남음)
        [해결] significant_support(): 저가 중 ±2% 안에 2번 이상 지지받은
               가격만 '진짜 지지'로 인정. 폭락 바닥 하나는 제외.
               손절 우선순위: 의미있는 지지 → 20일선 -2% → (폴백) 단순 저점.
        [검증] 폭락바닥(38,400) 무시 → 박스지지(49,000)로 손절. 리스크 30%→~12%.
v4.18.0 박스돌파도 '의미있는 저항(꼬리 제외)'으로 박스 상단 계산.
        [문제] 박스돌파 함수는 박스 상단을 단순 max(고가)로 잡아서,
               동진쎄미켐 5/26 꼬리(72,200)를 박스 천장으로 오인.
               → 박스폭 32~42%로 부풀려져 탈락 + 돌파여부 false.
        [해결] v4.17.0의 significant_resistance(2번+ 닿은 저항)를
               박스돌파에도 적용. 꼬리 제외하고 진짜 박스천장 인식.
        [검증] 꼬리(72,200) 제외 → 박스상단 65,000, 박스폭 42%→12%로 정상화.
        [참고] 포스코스틸리온(058430)은 유니버스 미등록이라 별도. 추가 필요시 요청.
v4.17.2 진단 API에 박스돌파(boxbreak) + 박스/거래량 지표 추가.
        modes에 boxbreak 통과/탈락 표시. indicators에 거래량배수(vs50일),
        120일선 이격, 박스20/40/60 폭·상단·돌파여부 추가.
        → 박스돌파/돌파임박 탈락 이유를 정확히 진단 가능.
v4.17.1 진단 API가 접미사(.KS/.KQ) 없이도 자동 매칭.
        [문제] /api/debug/005290 → "데이터 없음". 실제 코드는 005290.KQ인데
               접미사 없이 조회해서 네이버가 못 찾음. (데이터 문제 아님)
        [수정] 숫자코드만 입력하면 유니버스에서 .KS/.KQ 자동으로 찾아 붙임.
               이제 /api/debug/005290 → 005290.KQ(동진쎄미켐) 자동 매칭.
v4.17.0 피벗을 '여러 번 닿은 의미있는 저항'으로 (오버슈팅 꼬리 제외). 전모드 적용.
        [문제] 피벗 = 단순 최고가 → 긴 꼬리 하나(오버슈팅)를 천장으로 오인.
               동진쎄미켐: 진짜 천장 64~65k인데 5/26 꼬리 72,200을 피벗으로
               잡아 현재가가 -11%로 멀어져 돌파임박에서 탈락.
        [해결] significant_resistance(): 구간 고가 중 ±2% 안에 2번 이상 닿은
               가격만 '진짜 저항'으로 인정. 1번만 튀어나온 꼬리는 천장에서 제외.
               그런 저항이 없으면(진짜 신고가 추세) 단순 최고가로 폴백.
               → 전고(중기) 피벗에 적용. 모든 모드가 더 정확한 천장 사용.
        [검증] 꼬리(72,200) 무시하고 박스천장(65,000) 잡음 확인.
               신고가 추세 폴백 정상. 전 모드 스모크테스트 통과.
v4.16.1 박스돌파 가짜 돌파 수정 (모더나 오탐).
        [문제] 박스 상단을 '종가'로 잡아서, 진짜 천장(고가)보다 낮게 인식.
               모더나(고가천장 56, 현재 55.4 = 아직 아래)가 "돌파"로 오탐.
        [수정] ① 박스 고점/저점을 고가(high)/저가(low) 기준으로 → 진짜 천장 인식
               ② 돌파 판정 엄격화: 박스 상단 +0.5% 이상 확실히 넘어야 인정
                  (천장 코앞·살짝 닿음은 박스돌파 아님 → 돌파임박 영역)
        [검증] 천장 아래 종목 제외 / 진짜 돌파만 통과 확인.
v4.16.0 돌파임박에 '박스 상단 두드림 횟수' 추가.
        [신규] 동진쎄미켐처럼 박스 상단(피벗)을 여러 번 두드리는 종목 구분.
               최근 20봉 중 고가가 피벗 ±2% 안에 닿은 횟수(연속은 1회로 묶음).
               미너비니/오닐: 저항을 여러 번 두드리면 매물벽 약해져 돌파확률↑.
               - 2회 이상이면 "👊 N번 두드림" 배지 + 점수 가점(최대 +10)
               - 같은 돌파임박 중 더 유망한(여러번 두드린) 종목이 위로.
v4.15.1 박스돌파 카드 표시 버그 수정 (undefined 떡칠).
        [문제] 박스돌파 결과가 눌림목 카드 양식으로 그려져서 undefined 표시.
               (프론트 카드 렌더링에 boxbreak 분기가 없어서 else로 빠짐)
        [수정] 카드 지표/배지/거래량줄/스파크/일지라벨에 boxbreak 분기 추가.
               박스돌파 전용 표시: 연장도/거래량/박스폭/피벗(박스상단 N일)/
               박스돌파 N일 배지/장중돌파 미확정 배지.
v4.15.0 📦 박스돌파(boxbreak) 모드 신규 추가.
        [신규] 국장에서 자주 나오는 '박스 탈출 / 삼각수렴 돌파' 패턴 전용 탭.
               - 20/40/60봉 박스를 모두 검사, 하나라도 돌파면 포착
               - 거래량 1.5배+ 동반 필수 (박스돌파의 핵심, 가짜 거름)
               - 120일선(장기선) 위 — "장기선 위 박스탈출은 크게 간다"
               - RS 70+ (강한 종목만)
               - 급등 포함(가온전선 +29%도 돌파면 OK)
               - 장중 돌파도 표시(미확정 배지)
               - 박스 좁고 길수록 + 거래량 클수록 점수 높음
               예: 예스티(박스탈출 거래량폭발), 가온전선(하락추세 상단돌파)
v4.14.0 피벗을 '고정된 베이스 저항'으로 변경 (신고가 쫓아다니던 문제 해결).
        [문제] 피벗=최근 N봉 고가 라서, 주가가 신고가 만들 때마다 피벗이
               따라 올라감. "57,000인 줄 알았는데 57,700이네?" 혼란.
               기준선 역할을 못 함.
        [해결] 피벗 계산 시 직전 2봉(오늘·어제 신고가 갱신 봉)을 제외하고,
               그 이전 베이스(횡보 구간)의 고점을 피벗으로. → 주가가 신고가를
               만들어도 피벗(과거 천장)이 고정됨. 진짜 저항선 역할.
        [검증] 오늘 55,000이든 59,000(신고가)이든 피벗 동일(56,000) 확인.
v4.13.1 RS 계산을 IBD/MarketSmith 정식 공식으로 교체 (트레이딩뷰 근접).
        [변경] 기존: 1/3/6/9/12개월 임의 가중(합 1.2, 비정규화)
               신규: IBD 정식 = 12개월을 3개월씩 4분기로 나눠
                     0.4×Q1(최근) + 0.2×Q2 + 0.2×Q3 + 0.2×Q4 (합 1.0)
                     = 최근 분기 2배 가중. 트레이딩뷰 RS Rating과 같은 공식.
        [한계] 모집단이 우리 유니버스(493개)라 트레이딩뷰(전체시장) 대비
               숫자는 다를 수 있음. 공식은 동일, 상대 순위 의미도 동일.
v4.13.0 돌파임박 필터를 v4.9.1 수준으로 되돌림 (폭넓게 보여주기 복원).
        [배경] 손절 정교화하려 필터를 자꾸 조여 한국 0개까지 갔음. 사용자가
               "돌파 직전 종목을 폭넓게 보여주던 게 더 좋았다. 손절은 직접 거른다"고
               하여 필터를 원복. (방법 2: 필터만 되돌리고 좋은 추가는 유지)
        [되돌림] RS 70→50, 베이스폭 필터 제거, 급등 제외 제거,
                 손절은 단순 방식(최근저점/20일선-2% 중 현재가 아래 최고)으로.
        [유지] 클라이맥스 과열경고(🛑/🔆), 손익비 R, 종목검색, 데이터캐시,
               검색창 여백 등 v4.10~4.12의 좋은 추가는 그대로.
v4.12.1 돌파임박 RS 하한 50→70 (주도주만) + 베이스폭 15→12% (손절 타이트).
        [문제] RS 50~69 약한 종목(GS,기아,KT&G 등)이 떠서 주도주가 아님.
               손절도 13~14%로 여전히 깊었음.
        [수정] RS 하한 70 (미너비니 주도주 기준). 약한 종목 원천 제외.
               베이스폭 12%로 조여 타이트한 VCP만. RS로 거른 강한 종목 중
               좁은 베이스만 남아 손절 자연히 5~8%로.
        [효과] 종목 수↓ 질↑. RS 70+ & 타이트 베이스 = 진짜 미너비니 셋업.
v4.12.0 베이스폭 필터 완화(한국종목 전멸 수정) + 클라이맥스 매도경고 추가.
        [수정] v4.11.0 베이스폭 12%/15봉 필터가 한국 종목을 전부 걸러버림
               (한국 변동성이 커서). → 15%/10봉으로 완화. SK하이닉스류
               급등 종목은 여전히 제외되되, 잔잔한 한국 종목은 통과.
        [신규] 미너비니식 클라이맥스(과열/매도) 경고 — 모든 모드 카드에 표시.
               급등은 매수 아닌 '경계' 신호라는 미너비니 철학 반영.
               감지: 포물선급등(10봉+30%)/이평과열(20일선+25%)/최대급락일/
                     소진성거래량(60봉최대+음봉)/RSI과열(80+).
               🛑과열(danger: 최대급락·소진거래량) / 🔆과열(caution: 그 외)
v4.11.1 진단 API 강화 — 모드별 통과/탈락 + 지표 스냅샷.
        - /api/debug/{ticker} 가 7개 모드 각각 통과/탈락을 보여줌
        - 정배열 여부, 200일선 이격, 최근5봉 상승률, 베이스폭 등 지표 표시
        - "왜 이 종목이 이 탭에 없지?" 질문에 정확히 답하는 도구
v4.11.0 돌파임박 = '잔잔한 베이스 + 천장 코앞'으로 명확화 (길 B).
        [문제] 손절 공식을 아무리 바꿔도 깊게 나옴(SK하이닉스 22%, 현대 48%).
               근본 원인: 손절이 아니라 필터였음. '급등으로 천장 도달'한 종목이
               잔뜩 잡혀서, 어떤 손절을 써도 멀 수밖에 없었음.
        [해결] 돌파임박 필터에 '잔잔한 베이스' 조건 추가:
               · 베이스 폭(최근 15봉 고저폭) ≤ 12% — 넓으면 급등/난조로 제외
               · 최근 5봉 상승률 ≤ 10% — 이미 급등한 종목 제외
        [결과] 신세계/현대백화점/SK하이닉스처럼 급등 중인 종목은 자동 제외.
               남는 종목은 천장 아래 조용히 횡보 → 손절 자연히 5~8% 타이트.
        [눌림목과 차이] 눌림목=천장에서 먼 깊은 조정 / 돌파임박=천장 코앞 얕은 횡보
v4.10.3 돌파임박 손절을 ATR → 베이스 저점 기반으로 변경 (돌파매매 정석).
        [진단 결과] /api/debug로 확인: SK하이닉스 ATR 7.87%는 버그가 아니라
                    실제로 최근 2주 변동성이 극심했음(6/8 -7.7%, 6/9 +15.9%).
                    중앙값으로도 안 줄어든 이유 = 이상치가 아니라 전반적 고변동성.
        [변경] 손절 = 최근 15봉 베이스(횡보 구간) 저점.
               종목 변동성(ATR)이 아니라 '이 돌파 셋업이 무너지는 지점'.
               · 잔잔한 베이스 후 천장 근접 = 손절 타이트(5%) = 좋은 셋업
               · 급등으로 천장 도달(SK하이닉스) = 손절 깊음(22%) ⚠️ = 부적합
        [효과] 손절% 자체가 셋업 품질 지표가 됨. 급등 도달 종목이 자동 걸러짐.
v4.10.2 손절 원인 진단용 API 추가 + 검색창 여백 수정.
        - v4.10.1 중앙값 적용 후에도 한국 종목 손절 15~22% 지속 → ATR 계산이
          아니라 네이버 High/Low 데이터 자체를 의심. 미국(yfinance)은 정상.
        - 진단 API: /api/debug/{ticker} → 최근 14일 OHLC 원본 + TR 분해 확인
          (예: /api/debug/000660.KS 를 브라우저에서 열어 데이터 보고)
        - 검색창 좌우 여백을 카드 그리드와 동일하게(24px/모바일16px) 맞춤
v4.10.1 ATR 손절 버그 수정 — 평균→중앙값 (이상치 강건).
        [문제] 돌파임박 종목 손절이 죄다 10~31%로 비정상. 삼성SDS 31.7%.
               원인: ATR을 '평균(mean)'으로 계산 → 최근 급등/급락 며칠의
               거대한 변동폭이 14일 평균을 통째로 끌어올림. 돌파임박 종목은
               본질적으로 최근 크게 움직인 종목이라 전부 부풀려졌음.
        [해결] ATR을 '중앙값(median)'으로 변경 → 급등 며칠을 무시.
               잔잔한 종목 손절 ~3%, 보통 ~5%, 변동성큰 ~8%로 정상화.
               임의 통일이 아니라 종목별 진짜 변동성대로 차등 적용.
v4.10.0 돌파임박 손절을 ATR 기반(변동성 반영)으로 변경 + 피벗 기준 통일.
        [문제] 손절을 현재가 기준 지지선(20일선/옛저점)으로 잡아, 피벗 진입
               가정과 따로 놀았음. 게다가 모든 종목 동일 기준이라 변동성 무시.
        [변경]
        - 손절 = 피벗 - (ATR×2). 종목 변동성에 따라 손절폭 자동 조절
          · 잔잔한 종목 → 타이트, 변동성 큰 종목 → 넓게
        - 리스크%를 피벗 기준으로 통일 → 라벨 '(피벗진입)'이 실제와 일치
        - 손절 깊으면(>8%) ⚠️ 경고 → 변동성 과한 종목 걸러짐(예: HL만도)
        - scanner: atr() 함수 추가
v4.9.1  종목 검색 기능 추가 (프론트만).
        - 스캔된 카드 중에서 종목명/코드로 실시간 필터링
        - 서버 부하 없음 (이미 받은 목록에서 거름). 일지 탭에선 숨김
v4.9.0  손익비(R) 표시 + 리스크 '피벗진입 가정' 명시.
        - 리스크%가 '피벗에서 진입' 가정임을 라벨로 명시 (현재가 아님)
        - 손익비 R 추가: (목표-피벗)/(피벗-손절). 목표=전고 우선, 없으면 2R
        - 2R 이상 초록, 1.5 미만 빨강 경고. 진입 가치 한눈에
        - scanner: rr_info() 헬퍼, 4개 모드에 target/rr/target_basis
v4.8.0  데이터/필터 분리로 데이터 소스 호출 대폭 감소 (차단 방지).
        - 종목 일봉은 시장 단위로 1번만 받아 캐시(_data_cache), 모드 전환 시 재호출 안 함
        - 모드 7개 다 눌러도 네이버 호출은 시장당 1번 (기존 대비 1/7)
        - 동시 호출 6개로 제한(세마포어) + executor 워커 12->8 축소
v4.7.1  탭 순서 변경: 돌파임박을 첫 번째(기본)로, 눌림목 두 번째.
v4.7.0  즐겨찾기(★) 기능 + 탭 순서 변경.
        - 카드 별 버튼으로 즐겨찾기 토글, 즐겨찾기는 카드 최상단 고정
        - 서버 저장(favorites_user.txt), 기기 바뀌어도 유지
        - 탭 순서: 눌림목 → 돌파임박 → 돌파 → ...
v4.6.1  거래량 + 거래대금 표시 추가 (눌림목/돌파/돌파임박/추세전환 카드).
        거래대금 = 종가 × 거래량 근사. 한국 억/조원, 미국 $M/$B 단위.
        - scanner: volume_info() 헬퍼, 4개 모드 dict에 volume/turnover
        - index.html: fmtVolume/fmtTurnover + 카드 거래량 줄
v4.6.0  '돌파 임박(imminent)' 모드 신설.
        천장(피벗) 바로 아래 -5%~0%까지 올라왔지만 아직 안 뚫은 종목 포착.
        돌파 전날 미리 잡으려는 용도. 거래량 수축은 가점(필수 아님).
        점수 = 피벗근접 35 + 거래량수축 20 + VCP 20 + RS 15 + 200일선위 10.
        - scanner: analyze_imminent() + IMMINENT_CONFIG
        - app: imminent 모드 등록 + is_kr 전달
        - index.html: 🎯돌파임박 탭/카드/배지/설명
v4.5.1  '장중 돌파 ⚠️ 미확정' 배지 추가 (눌림목/추세전환 모드).
        한국 종목이 장중에 하락 추세선을 넘었지만 종가 확정 전인 상태를
        노란 배지로 경고. 가짜 돌파(위꼬리 후 마감 하락) 주의 유도.
        - scanner: is_kr_market_open(), select_pivot에 tl_break_intraday 반환
        - app: pullback/turnaround 모드에 is_kr 전달
v4.5.0  한국 종목(.KS/.KQ) 데이터 소스를 yfinance → 네이버(naver_kr)로 전환.
        yfinance 일봉의 한국 장중 지연(전일 종가 고정) 문제 해결.
        과거 일봉 + 장중 현재가 보정. 미국 종목은 yfinance 유지.
v4.4.2  (이전 버전)
"""
import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from scanner import analyze, analyze_turnaround, analyze_leader, analyze_super, analyze_breakout, analyze_surge, analyze_imminent, analyze_boxbreak, rs_raw_score, to_rs_rank, climax_warning
from sectors import get_sector
from universe import get_universe, load_alerts
import naver_kr
import fundamentals as fundamentals_mod

app = FastAPI(title="눌림목 스캐너")

VERSION = "v4.30.0"
CACHE_TTL = 600              # 모드별 결과 캐시 (10분)
DATA_TTL = 600              # 시장별 원본 데이터 캐시 (10분) — 모드 전환 시 재호출 안 함
MAX_CONCURRENT_FETCH = 6    # 데이터 소스 동시 호출 제한 (차단 방지)
_cache: dict[str, dict] = {}
_data_cache: dict[str, dict] = {}
_executor = ThreadPoolExecutor(max_workers=8)


def _fetch(ticker: str):
    # 한국 종목(.KS/.KQ)은 네이버, 그 외는 yfinance
    if naver_kr.is_kr(ticker):
        try:
            df = naver_kr.fetch(ticker)
            if df is None or df.empty:
                return None
            return df
        except Exception:
            return None
    try:
        df = yf.Ticker(ticker).history(period="1y", interval="1d", auto_adjust=True)
        if df is None or df.empty:
            return None
        return df
    except Exception:
        return None


def _ret_pct(close, days):
    c = close.dropna()
    if len(c) < days + 1:
        return None
    past = float(c.iloc[-days - 1])
    return float(c.iloc[-1]) / past - 1 if past > 0 else None


async def _fetch_market_data(market: str) -> dict:
    """시장 단위로 종목 일봉 + RS 계산. 모드와 무관하므로 시장별로 캐시해 재사용.
    여기서만 네이버/야후를 호출한다 (모드 전환 시 재호출 안 함)."""
    cache_key = f"data:{market}"
    cached = _data_cache.get(cache_key)
    if cached and time.time() - cached["ts"] < DATA_TTL:
        return cached

    universe = get_universe(market)
    loop = asyncio.get_event_loop()
    tickers = list(universe.keys())

    # 동시 호출 수를 제한해 데이터 소스에 부담을 덜 줌 (차단 방지)
    sem = asyncio.Semaphore(MAX_CONCURRENT_FETCH)

    async def fetch_one(t):
        async with sem:
            return await loop.run_in_executor(_executor, _fetch, t)

    dfs = await asyncio.gather(*[fetch_one(t) for t in tickers])
    data = {t: df for t, df in zip(tickers, dfs) if df is not None}

    # RS 등급 + RS 모멘텀 (시장별 백분위)
    kr, us = {}, {}
    kr3, us3, kr12, us12 = {}, {}, {}, {}
    for t, df in data.items():
        is_kr = t.endswith((".KS", ".KQ"))
        (kr if is_kr else us)[t] = rs_raw_score(df["Close"])
        (kr3 if is_kr else us3)[t] = _ret_pct(df["Close"], 63)
        (kr12 if is_kr else us12)[t] = _ret_pct(df["Close"], 252)
    rs_ranks = {**to_rs_rank(kr), **to_rs_rank(us)}
    rank3 = {**to_rs_rank(kr3), **to_rs_rank(us3)}
    rank12 = {**to_rs_rank(kr12), **to_rs_rank(us12)}
    rs_moms = {t: rank3[t] - rank12[t] for t in data if t in rank3 and t in rank12}

    bundle = {
        "universe": universe,
        "data": data,
        "rs_ranks": rs_ranks,
        "rs_moms": rs_moms,
        "ts": time.time(),
    }
    _data_cache[cache_key] = bundle
    return bundle


async def run_scan(market: str, mode: str) -> dict:
    # 데이터는 시장 단위 캐시에서 (모드 바뀌어도 재호출 안 함)
    bundle = await _fetch_market_data(market)
    universe = bundle["universe"]
    data = bundle["data"]
    rs_ranks = bundle["rs_ranks"]
    rs_moms = bundle["rs_moms"]

    fn = {"turnaround": analyze_turnaround, "leader": analyze_leader, "super": analyze_super, "breakout": analyze_breakout, "surge": analyze_surge, "imminent": analyze_imminent, "boxbreak": analyze_boxbreak}.get(mode, analyze)
    supports_intraday = mode in ("pullback", "turnaround", "imminent", "boxbreak")  # is_kr 인자를 받는 모드
    alerts = load_alerts()
    hits = []
    for t, df in data.items():
        is_kr = t.endswith((".KS", ".KQ"))
        kwargs = {"rs_rank": rs_ranks.get(t), "rs_mom": rs_moms.get(t)}
        if supports_intraday:
            kwargs["is_kr"] = is_kr
        result = fn(df, **kwargs)
        if result is None:
            continue
        mkt = "KR" if is_kr else "US"
        alert_kind = alerts.get(t.upper())
        # 미너비니식 클라이맥스(과열/매도) 경고 — 모든 모드에 부착
        cw = climax_warning(df["Close"], df["High"], df["Low"], df["Volume"])
        hits.append({"ticker": t, "name": universe[t], "market": mkt,
                     "sector": get_sector(t), "alert": alert_kind,
                     "climax": cw["climax"], "climax_reasons": cw["reasons"],
                     "climax_level": cw["level"], **result})

    hits.sort(key=lambda x: (x.get("triggered", False), x.get("setup_score") or x["score"]), reverse=True)

    # 섹터 요약: 2개 이상 잡힌 섹터를 개수 내림차순 (기타 제외)
    from collections import Counter
    sec_count = Counter(h["sector"] for h in hits if h["sector"] != "기타")
    sector_summary = [
        {"sector": s, "count": n} for s, n in sec_count.most_common() if n >= 2
    ]

    warn_count = sum(1 for h in hits if h.get("alert") or h.get("risk_warn"))

    return {
        "version": VERSION,
        "market": market,
        "mode": mode,
        "scanned": len(universe),
        "fetched": len(data),
        "hits": hits,
        "sector_summary": sector_summary,
        "warn_count": warn_count,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ts": time.time(),
    }


@app.get("/api/scan")
async def scan(market: str = "all", mode: str = "imminent", refresh: bool = False):
    market = market if market in ("kr", "us", "all") else "all"
    mode = mode if mode in ("pullback", "turnaround", "leader", "super", "breakout", "surge", "imminent", "boxbreak") else "pullback"
    key = f"{market}:{mode}"
    favs = load_favorites()
    cached = _cache.get(key)
    if cached and not refresh and time.time() - cached["ts"] < CACHE_TTL:
        return JSONResponse({**cached, "favorites": favs, "cached": True})
    result = await run_scan(market, mode)
    _cache[key] = result
    return JSONResponse({**result, "favorites": favs, "cached": False})


ALERTS_USER_PATH = os.path.join(os.path.dirname(__file__), "alerts_user.txt")


@app.get("/api/alerts")
async def get_alerts():
    return JSONResponse(load_alerts())


@app.post("/api/alerts")
async def add_alert(request: Request):
    """대시보드에서 경보 종목 추가/삭제. {ticker, kind} 또는 {ticker, remove:true}"""
    body = await request.json()
    ticker = (body.get("ticker") or "").upper().strip()
    if not ticker:
        return JSONResponse({"ok": False, "error": "ticker 필요"}, status_code=400)
    kind = body.get("kind", "경보")
    remove = body.get("remove", False)

    # alerts_user.txt 읽기 → 수정 → 쓰기
    entries = {}
    if os.path.exists(ALERTS_USER_PATH):
        with open(ALERTS_USER_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(maxsplit=1)
                entries[parts[0].upper()] = parts[1] if len(parts) > 1 else "경보"
    if remove:
        entries.pop(ticker, None)
    else:
        entries[ticker] = kind
    with open(ALERTS_USER_PATH, "w", encoding="utf-8") as f:
        f.write("# 대시보드에서 추가한 경보 종목 (자동 생성)\n")
        for tk, kd in sorted(entries.items()):
            f.write(f"{tk} {kd}\n")
    # 캐시 무효화 (다음 스캔에 반영)
    _cache.clear()
    return JSONResponse({"ok": True, "alerts": entries})


# ── 즐겨찾기 (서버 저장, alerts와 동일 패턴) ──
FAVORITES_PATH = os.path.join(os.path.dirname(__file__), "favorites_user.txt")


def load_favorites() -> list[str]:
    """즐겨찾기 티커 목록 (대문자)."""
    favs = []
    if os.path.exists(FAVORITES_PATH):
        with open(FAVORITES_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                favs.append(line.split()[0].upper())
    return favs


@app.get("/api/favorites")
async def get_favorites():
    return JSONResponse(load_favorites())


@app.post("/api/favorites")
async def toggle_favorite(request: Request):
    """즐겨찾기 추가/삭제. {ticker, remove?:bool}. remove 없으면 토글."""
    body = await request.json()
    ticker = (body.get("ticker") or "").upper().strip()
    if not ticker:
        return JSONResponse({"ok": False, "error": "ticker 필요"}, status_code=400)
    favs = load_favorites()
    remove = body.get("remove")
    if remove is None:
        remove = ticker in favs  # 없으면 토글
    if remove:
        favs = [f for f in favs if f != ticker]
    elif ticker not in favs:
        favs.append(ticker)
    with open(FAVORITES_PATH, "w", encoding="utf-8") as f:
        f.write("# 대시보드 즐겨찾기 (자동 생성)\n")
        for tk in favs:
            f.write(f"{tk}\n")
    return JSONResponse({"ok": True, "favorites": favs})


@app.get("/api/debug/{ticker}")
async def debug_ticker(ticker: str):
    """진단용: 종목의 최근 OHLC 원본 + ATR 분해 + 각 모드 통과/탈락 여부.
    예: /api/debug/347850.KQ  (배포 후 브라우저에서 열기)"""
    import pandas as _pd
    from scanner import (analyze, analyze_turnaround, analyze_imminent,
                         analyze_breakout, analyze_leader, analyze_super, analyze_surge,
                         analyze_boxbreak)
    # 접미사(.KS/.KQ) 없이 숫자코드만 입력해도 유니버스에서 자동 매칭
    _uni = get_universe(None)
    if ticker not in _uni:
        for suf in (".KS", ".KQ"):
            if (ticker + suf) in _uni:
                ticker = ticker + suf
                break
    df = _fetch(ticker)
    if df is None or df.empty:
        return JSONResponse({"error": "데이터 없음", "ticker": ticker})
    is_kr = ticker.endswith((".KS", ".KQ"))
    h, lo, c = df["High"], df["Low"], df["Close"]
    prev_c = c.shift(1)
    tr = _pd.concat([h - lo, (h - prev_c).abs(), (lo - prev_c).abs()], axis=1).max(axis=1)
    close = float(c.iloc[-1])

    # 각 모드 통과 여부 (RS 정보 없이 단독 호출 — 대략적 판정)
    modes = {}
    for name, fn in [("pullback", analyze), ("turnaround", analyze_turnaround),
                     ("imminent", analyze_imminent), ("breakout", analyze_breakout),
                     ("boxbreak", analyze_boxbreak),
                     ("leader", analyze_leader), ("super", analyze_super), ("surge", analyze_surge)]:
        try:
            kwargs = {"rs_rank": 80, "rs_mom": 5}
            if name in ("pullback", "turnaround", "imminent", "boxbreak"):
                kwargs["is_kr"] = is_kr
            res = fn(df, **kwargs)
            modes[name] = "통과" if res is not None else "탈락"
        except Exception as e:
            modes[name] = f"에러: {e}"

    # 지표 스냅샷 (왜 탈락했는지 직접 보기 위한 값들)
    ma20 = float(c.rolling(20).mean().iloc[-1])
    ma60 = float(c.rolling(60).mean().iloc[-1])
    ma200 = float(c.rolling(200).mean().iloc[-1]) if len(c) >= 200 else None
    ret5 = (close / float(c.iloc[-6]) - 1) * 100 if len(c) >= 6 else None
    base_hi = float(h.iloc[-15:].max()); base_lo = float(lo.iloc[-15:].min())
    base_width = (base_hi - base_lo) / close * 100
    # 박스돌파 진단용 지표
    v = df["Volume"]
    vol_today = float(v.iloc[-1])
    vol_avg50 = float(v.iloc[-51:-1].mean()) if len(v) >= 51 else float(v.mean())
    vol_mult = round(vol_today / vol_avg50, 2) if vol_avg50 > 0 else None
    ma120 = float(c.rolling(120).mean().iloc[-1]) if len(c) >= 120 else None
    box_info = {}
    for win in (20, 40, 60):
        if len(h) >= win + 1:
            bh = float(h.iloc[-(win + 1):-1].max())
            bl = float(lo.iloc[-(win + 1):-1].min())
            box_info[f"박스{win}_폭%"] = round((bh - bl) / bh * 100, 1) if bh > 0 else None
            box_info[f"박스{win}_상단"] = round(bh)
            box_info[f"박스{win}_돌파여부"] = close > bh * 1.005

    return JSONResponse({
        "ticker": ticker,
        "close": round(close),
        "modes": modes,
        "indicators": {
            "ma20": round(ma20), "ma60": round(ma60),
            "ma200": round(ma200) if ma200 else None,
            "ma120": round(ma120) if ma120 else None,
            "close_vs_ma200_pct": round((close - ma200) / ma200 * 100, 1) if ma200 else None,
            "close_vs_ma120_pct": round((close - ma120) / ma120 * 100, 1) if ma120 else None,
            "정배열(20>60>200)": (ma200 is not None and ma20 > ma60 > ma200 and close > ma200),
            "ret5_pct": round(ret5, 1) if ret5 is not None else None,
            "base_width_15_pct": round(base_width, 1),
            "거래량배수(vs50일)": vol_mult,
            "박스돌파_기준": "거래량 1.5배+ & 120일선 위 & RS70+ & 박스폭30%이내 & 상단+0.5%돌파",
            **box_info,
        },
        "atr_median_pct": round(float(tr.iloc[-14:].median()) / close * 100, 2),
    })


_indices_cache: dict = {}
_INDICES_TTL = 300  # 지수+레짐 캐시 5분 (레짐 계산이 무거워 길게)
_fund_cache: dict = {}
_FUND_TTL = 3600    # 펀더멘털 캐시 1시간


def _fetch_nasdaq() -> dict | None:
    """나스닥 종합(^IXIC) 현재값 + 등락. yfinance."""
    try:
        df = yf.Ticker("^IXIC").history(period="5d", interval="1d", auto_adjust=False)
        if df is None or len(df) < 2:
            return None
        last = float(df["Close"].iloc[-1])
        prev = float(df["Close"].iloc[-2])
        chg = last - prev
        pct = (last / prev - 1) * 100 if prev > 0 else 0.0
        return {"name": "나스닥", "value": round(last, 2),
                "change": round(chg, 2), "change_pct": round(pct, 2)}
    except Exception:
        return None


def _index_regime(code: str) -> dict | None:
    """지수 일봉으로 시장 레짐 판정. code: 'KOSPI'|'KOSDAQ'.
    - 20일선 위 + 20일선 우상향 → 'good'
    - 60일선 아래 → 'bad'
    - 그 외 → 'neutral'
    나스닥(^IXIC)은 yfinance로 별도 처리."""
    try:
        if code == "^IXIC":
            df = yf.Ticker("^IXIC").history(period="6mo", interval="1d", auto_adjust=False)
            close = df["Close"] if df is not None and not df.empty else None
        else:
            hist = naver_kr.fetch_index_history(code, days=160)
            close = hist["Close"] if hist is not None and not hist.empty else None
        if close is None or len(close) < 60:
            return None
        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean()
        cur = float(close.iloc[-1])
        m20 = float(ma20.iloc[-1])
        m60 = float(ma60.iloc[-1])
        m20_prev = float(ma20.iloc[-6])   # 5일 전 20일선 (기울기)
        rising20 = m20 > m20_prev
        if cur < m60:
            regime, txt = "bad", "비우호 (신규진입 자제)"
        elif cur > m20 and rising20:
            regime, txt = "good", "우호 (진입 환경 양호)"
        else:
            regime, txt = "neutral", "중립 (선별 진입)"
        return {"regime": regime, "regime_txt": txt,
                "above_ma20": cur > m20, "above_ma60": cur > m60}
    except Exception:
        return None


@app.get("/api/indices")
async def indices():
    """상단 지수 바: 나스닥 / 코스피 / 코스닥. 60초 캐시."""
    now = time.time()
    if _indices_cache and now - _indices_cache.get("ts", 0) < _INDICES_TTL:
        return JSONResponse(_indices_cache["data"])

    loop = asyncio.get_event_loop()
    nasdaq, kospi, kosdaq, r_kospi, r_kosdaq, r_nasdaq = await asyncio.gather(
        loop.run_in_executor(_executor, _fetch_nasdaq),
        loop.run_in_executor(_executor, naver_kr.fetch_index, "KOSPI"),
        loop.run_in_executor(_executor, naver_kr.fetch_index, "KOSDAQ"),
        loop.run_in_executor(_executor, _index_regime, "KOSPI"),
        loop.run_in_executor(_executor, _index_regime, "KOSDAQ"),
        loop.run_in_executor(_executor, _index_regime, "^IXIC"),
    )
    # 레짐 정보 병합
    if kospi and r_kospi: kospi.update(r_kospi)
    if kosdaq and r_kosdaq: kosdaq.update(r_kosdaq)
    if nasdaq and r_nasdaq: nasdaq.update(r_nasdaq)
    # 순서: 코스피, 코스닥, 나스닥 (국내 먼저)
    data = {"indices": [x for x in (kospi, kosdaq, nasdaq) if x]}
    _indices_cache["ts"] = now
    _indices_cache["data"] = data
    return JSONResponse(data)


@app.get("/api/fundamentals/{ticker}")
async def fundamentals(ticker: str):
    """카드 '밸류 보기' 클릭 시 온디맨드. 종목 1개 펀더멘털. 캐시 1시간."""
    now = time.time()
    cached = _fund_cache.get(ticker)
    if cached and now - cached["ts"] < _FUND_TTL:
        return JSONResponse(cached["data"])
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(_executor, fundamentals_mod.get_fundamentals, ticker)
    result = data or {"error": "데이터 없음"}
    _fund_cache[ticker] = {"ts": now, "data": result}
    return JSONResponse(result)


# ── 매매 일지 (서버 저장, 기기 간 동기화) ──
import json as _json
JOURNAL_PATH = os.path.join(os.path.dirname(__file__), "journal_user.json")


def load_journal() -> list:
    """매매 일지 전체 (객체 배열)."""
    if os.path.exists(JOURNAL_PATH):
        try:
            with open(JOURNAL_PATH, encoding="utf-8") as f:
                data = _json.load(f)
                return data if isinstance(data, list) else []
        except (ValueError, OSError):
            return []
    return []


@app.get("/api/journal")
async def get_journal():
    return JSONResponse(load_journal())


@app.post("/api/journal")
async def save_journal(request: Request):
    """일지 전체를 통째로 저장(덮어쓰기). body = 일지 객체 배열."""
    body = await request.json()
    if not isinstance(body, list):
        return JSONResponse({"ok": False, "error": "배열 필요"}, status_code=400)
    try:
        with open(JOURNAL_PATH, "w", encoding="utf-8") as f:
            _json.dump(body, f, ensure_ascii=False, indent=1)
    except OSError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, "count": len(body)})


@app.get("/")
async def index():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")
