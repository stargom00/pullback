"""99일선 기준봉(장대양봉) 뒤 "좋은 되돌림" 확인봉 → 다음 날 시가 진입 EV.

사전등록: docs/ma99_good_pullback.md. **600일선 좋은 되돌림 스크립트(2026-10-09_ma600_good_pullback.py)를 import해서 선 길이만 99로 바꾼다**
— 매물대·이평선·눌림·확인봉·기한·진입·손절·청산·겹침·판정(z ≥ 2.69)·기록 항목은 그 모듈 그대로.
**⚠️ ④ LTF 가격 반응은 과거 분봉이 없어 일봉 반전봉으로 대체했다.**
실행: MEAS_CACHE=<3,000일 캐시.pkl> python3 scripts/measurements/2026-10-09_ma99_good_pullback.py
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("ma_good_pullback", os.path.join(HERE, "2026-10-09_ma600_good_pullback.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

m.MA_N = 99                       # 선 길이만 바꾼다(이력 기준 = 정제 유효봉 100개 — classify가 MA_N + 1을 본다)
m.line.MA_N = 99                  # 선 모듈의 후보 봉 거름(candidate_bars)이 MIN_BARS를 보므로 같이 맞춘다
m.line.MIN_BARS = 100             # (안 맞추면 600일선 기준 601봉 미만 후보가 조용히 빠진다 — 테스트로 고정)
m.OUT = os.path.join(HERE, os.path.basename(__file__).replace(".py", ".results.json"))

if __name__ == "__main__":
    m.main()
