"""99일선 돌파 장대양봉(A) vs 99일선 아래 장대양봉(C) — 다음 날 시가 진입 EV.

사전등록: docs/ma99_bull_candle_breakout.md. 2026-10-09 MA99 돌파 후 되돌림 측정(docs/ma99_breakout_retest.md, 기각)과 다른 가설 —
이번엔 장대양봉 + 99일선 **종가 돌파**, 진입 = 다음 날 시가.
**600일선 스크립트(2026-10-09_ma600_bull_candle_breakout.py)를 import해서 선 길이만 99로 바꾼다** — 장대양봉 정의, A/C 정의, 진입·손절
(장대양봉 시가, 저가 터치 −1R)·+2R/60봉, 겹침 건너뛰기, 저유동성, 반분, 판정식(z ≥ 2.58, Bonferroni 5), 3,000일 데이터, 기록 항목
(D+63·연도별·다른 선(600) 기준으로도 A인 건수)은 그 모듈의 함수·값 그대로다. 이력 기준 = 정제 유효봉 100개 이상(전일·당일 MA99).
실행 시각: harness.check_run_window(["KR"]). 실행: MEAS_CACHE=<3,000일 캐시.pkl> python3 scripts/measurements/2026-10-09_ma99_bull_candle_breakout.py
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("ma_line_bull_candle", os.path.join(HERE, "2026-10-09_ma600_bull_candle_breakout.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

# ── 바꾸는 것: 선 길이 하나(과 그에 딸린 이력 기준·비교 선·출력 경로) ──
m.MA_N = 99                       # "선 길이만 99로 바꾼다"
m.MIN_BARS = m.MA_N + 1           # "이력 기준은 정제 유효봉 100개 이상"
m.OTHER_N = 600                   # 기록 전용 — 같은 장대양봉이 600일선 기준으로도 A인지
m.OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))

if __name__ == "__main__":
    m.main()
