"""일지 카드 "돌파 기록" 3칸 계산 (v5.274, 사용자 지시).

순수 계산 모듈 — 네트워크·파일·전역 상태 없음(`abc_screener.py`와 같은 성격).

확정된 정의(2026-09-20):
  · **D+0 = 종가 > 피벗인 첫 날.** 고가 터치는 돌파가 아니다. D+1~3도 종가 기준.
  · 등록일(`since`) **이후**만 본다 — 등록 전 과거 돌파는 내 매매와 무관하다.
  · **D+0은 한 번 잡히면 안 바뀐다**(2026-09-20 재확정). 처음엔 "종가가 피벗
    아래로 내려가면 리셋하고 다음 돌파가 새 D+0"이었는데, 그러면 살아남은 D+0
    뒤의 봉은 **정의상 전부 피벗 위**라 ✓/✗의 ✗가 구조적으로 나올 수 없었다
    (64패턴 전수로 확인). **돌파 실패를 기록하는 게 목적**이므로 리셋을 버리고
    첫 D+0을 고정한다 — D+1~3 종가가 피벗 아래면 그대로 ✗로 남는다.
  · **빈칸 두 종류를 구분한다**(뭉치면 "데이터가 없다"와 "아직 안 왔다"가 같아
    보인다):
        "—"  아직 그 날이 안 지났다
        "?"  지났는데 봉이 없다(데이터 누락·조회 실패)

거래일 환산은 달력일의 68.5%를 쓴다 — `test_kr_scan_window.py`가 실측으로 쓰는
비율과 같은 값이다(1,900일 → 1,275봉).
"""
from __future__ import annotations

TRADING_DAY_RATIO = 0.685        # 달력일 → 거래일 환산(실측치, 위 docstring 참고)
VOL_AVG_BARS = 20                # 거래량 배수의 기준 평균 구간(임의값)
FOLLOW_DAYS = 3                  # D+1..D+3

NOT_YET = "—"                    # 아직 안 지남
MISSING = "?"                    # 지났는데 봉 없음


def _vol_mult(vol, i: int, avg_bars: int = VOL_AVG_BARS):
    """i번 봉의 거래량 ÷ **직전** avg_bars봉 평균.

    기준 평균에 i번 봉 자신을 넣으면 큰 거래량이 분모도 키워 배수가 눌린다.
    """
    lo = max(0, i - avg_bars)
    prev = vol[lo:i]
    if not len(prev):
        return None
    avg = float(sum(prev)) / len(prev)
    return round(float(vol[i]) / avg, 2) if avg > 0 else None


def analyze_breakout_record(dates, closes, volumes, pivot, since=None, asof=None):
    """일지 한 건의 돌파 기록.

    dates: 봉 날짜 리스트(date/datetime). closes/volumes: 같은 길이의 수열.
    pivot: 사용자가 입력한 피벗. since: 등록일(이 날짜 **이후** 봉만 본다).
    asof: 오늘(생략 시 마지막 봉 날짜) — "아직 안 지남"과 "봉 없음"을 가른다.

    반환:
        {"ok": bool, "reason": str|None,
         "d0_index": int, "d0_date": date,
         "failed": int,            # D+1~3 중 종가가 피벗 아래였던 날 수(= ✗ 개수)
         "d0_vol_mult": float|None,
         "follow": [{"day": 1, "vol_mult": .., "above": bool} | {"day":1,"blank":"—"} ...]}
    """
    n = len(closes)
    out = {"ok": False, "reason": None, "d0_index": None, "d0_date": None,
           "failed": 0, "d0_vol_mult": None, "follow": []}
    if pivot is None or not n or len(dates) != n or len(volumes) != n:
        out["reason"] = "봉 없음"
        return out

    start = 0
    if since is not None:
        # 등록일 **다음 봉부터**. 등록 당일 종가가 이미 피벗 위여도 그건
        # 등록 시점의 상태지 "내가 본 돌파"가 아니다.
        while start < n and dates[start] <= since:
            start += 1

    # ── D+0 = 종가가 피벗을 넘은 **첫** 날. 한 번 잡히면 안 바뀐다. ──
    d0 = None
    for i in range(start, n):
        if float(closes[i]) > float(pivot):
            d0 = i
            break

    if d0 is None:
        out["reason"] = "아직 돌파 없음"
        return out

    out.update(ok=True, d0_index=d0, d0_date=dates[d0],
               d0_vol_mult=_vol_mult(volumes, d0))

    # ── D+1..3 ──
    failed = 0
    last_date = dates[-1]
    ref = asof if asof is not None else last_date
    for k in range(1, FOLLOW_DAYS + 1):
        j = d0 + k
        if j < n:
            above = float(closes[j]) > float(pivot)
            if not above:
                failed += 1       # 돌파를 지켜내지 못한 날 — ✗로 남는다
            out["follow"].append({"day": k, "vol_mult": _vol_mult(volumes, j),
                                  "above": above})
            continue
        # 봉이 없다 — 아직 안 온 건가, 있어야 하는데 없는 건가?
        elapsed_cal = (ref - dates[d0]).days
        elapsed_bars = elapsed_cal * TRADING_DAY_RATIO
        blank = MISSING if elapsed_bars >= k else NOT_YET
        out["follow"].append({"day": k, "blank": blank})
    out["failed"] = failed
    return out
