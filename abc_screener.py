"""🔺 ABC 패턴 스크리너 (v5.267) — 더양봉맨식 A(긴 하락)·B(바닥 횡보)·C(단계) 판정.

**순수 계산 모듈이다.** 일봉 DataFrame 하나를 받아 판정 dict를 돌려주는 것이 전부 —
네트워크·파일·전역 상태에 기대지 않는다(sector_snapshot.py / theme_reignition.py와
같은 책임 분리). 저장·스케줄링·노출은 app.py가 한다.

⚠️ **이 탭은 관심 신호다. 진입 근거가 아니다.**
아래 임계값은 **전부 2026-09-18 시점의 초기 임의값**이며 측정·백테스트 근거가 없다
(CLAUDE.md "새 CONFIG 조건·임계값 도입 시 출처 기록" 4번 유형 — AI/사용자 판단으로
임의 설정). 측정 전까지 이 수치로 진입 판단을 하지 말 것.
"""
from __future__ import annotations

# ── 임계값 — 전부 한 곳에. 근거 없음(초기 임의값, 2026-09-18) ──────────
ABC_CONFIG = {
    # A: 긴 하락
    "a_lookback": 250,        # 고점 탐색 구간(봉) ≈ 52주
    "a_drop_min": 0.40,       # 고점→저점 하락폭 ≥ 40%
    "a_span_min": 40,         # 고점→저점 소요 ≥ 40봉
    # B: 바닥 횡보
    "b_min_bars": 20,         # 저점 **직후** 횡보로 인정할 최소 봉수
    "b_max_bars": 60,         # 같은 구간의 최대 — 20~60 중 가장 긴 것을 고른다
    "b_range_max": 0.25,      # 그 구간 고저 범위 ≤ 25%
    "b_ma200_band": 0.15,     # 구간 중앙값이 MA200 ±15% 안
    # C: 단계 (close vs MA200)
    "c0": (-0.15, -0.05),     # 대기
    "c1": (-0.05, 0.05),      # 벽앞 — **c2와 [0,+5%)에서 겹친다**(사용자 정의
                              #   C1 −5~+5 / C2 0~+20 그대로). 판정 순서가 C2를
                              #   먼저 보므로 겹침 구간은 C2다(= C1 실질 −5~0%).
                              #   **사용자 확정 2026-09-18**("C2 우선 유지. 확정").
                              #   test_c1_and_c2_overlap_is_resolved_toward_c2가
                              #   이 해소 방향을 고정한다 — 바꾸려면 그 테스트부터.
    "c2": (0.00, 0.20),       # 진돌이/가돌이 (돌파 동반)
    "c3_min": 0.20,           # 이탈
    "c2_vol_mult": 3.0,       # 진돌이 기준 거래량 배수(돌파봉/직전 5일평균)
    "vol_avg_bars": 5,
    "breakout_lookback": 60,  # 돌파봉 탐지 창(봉) — 이 안에 돌파가 없으면 "돌파 없음"
    "breakout_vol_window": 3, # 돌파봉 포함 N봉 중 **최대 거래량**으로 진돌이/가돌이
                              # 판정(임의값). 첫 교차봉이 소량이고 다음날 대량이
                              # 터지는 형태가 흔해, 교차봉 하나만 보면 그걸
                              # "가돌이"로 잘못 부른다(LS에코 09-15 2.09배 →
                              # 09-16 13.01배).
    # 매물대
    "supply_band": (1.00, 1.30),   # MA200 ~ MA200×1.3
    "supply_min_bars": 30,         # 그 구간에 과거 250봉 중 ≥30봉
    # 다른 셋업(ABC 아님) 판정
    "other_above_ma200_bars": 60,  # MA200 위 60봉 이상이면 박스/눌림
    # 기업 축
    "min_turnover_eok": 300,       # 판정일 거래대금(억) — close×volume
    "rev_yoy_min_quarters": 3,     # 최근 4분기 중 매출 YoY+ 분기 수
    "rev_yoy_window": 4,
    "eps_positive_quarters": 2,    # 최근 2분기 EPS 흑자
    "min_bars": 250,               # 판정에 필요한 최소 봉수
}

C_STAGES = ("C0 대기", "C1 벽앞", "C2 진돌이", "C2 가돌이", "C3 이탈")


def _ma(close, n: int):
    return float(close.iloc[-n:].mean()) if len(close) >= n else None


def _find_breakout(close, vol, ma200: float, cfg: dict):
    """최근 `breakout_lookback`봉 중 **MA200 아래→위로 넘어간 마지막 봉**.

    없으면 None("돌파 없음" — 60봉 내내 위에 있었다는 뜻).
    vol_mult는 **그 돌파봉의** 거래량 ÷ 직전 5일평균이다(오늘 거래량이 아니다) —
    진돌이/가돌이 라벨은 돌파 시점의 성격이고 이후 유지된다(사용자 확정).

    ⚠️ 비교 기준은 **각 봉 시점의 MA200**(이동값)이다. 처음엔 오늘의 MA200
    하나를 상수로 두고 과거 봉을 비교했는데, 그러면 "오늘 기준선"을 며칠 전에
    넘은 봉이 돌파로 잡혀 **돌파 시점과 거래량이 둘 다 틀렸다**
    (LS에코 2026-09-18: 잘못된 값 vol 2.09 / 3봉 전).
    """
    n = len(close)
    look = min(cfg["breakout_lookback"], n - 1)
    ma_series = close.rolling(200).mean()
    found = None
    for i in range(n - look, n):
        if i < 1:
            continue
        m = ma_series.iloc[i]
        if m != m:                      # NaN — 200봉 미만 구간
            continue
        if float(close.iloc[i - 1]) <= float(m) < float(close.iloc[i]):
            found = i
    if found is None:
        return None
    k = cfg["vol_avg_bars"]
    prev = vol.iloc[max(0, found - k):found]
    avg = float(prev.mean()) if len(prev) else 0.0
    # 돌파봉 포함 N봉 중 **최대 거래량** 봉으로 배수를 낸다(사용자 확정 (b)).
    # 기준 평균은 **돌파 직전 5일** 하나로 고정 — 최대 거래량 봉마다 기준을
    # 다시 잡으면 그 봉 직전에 이미 대량이 실린 경우 배수가 눌린다.
    w = cfg["breakout_vol_window"]
    seg = vol.iloc[found:min(n, found + w)]
    peak_rel = int(seg.values.argmax()) if len(seg) else 0
    peak_i = found + peak_rel
    return {"bars_ago": n - 1 - found,
            "vol_bar_ago": n - 1 - peak_i,
            "vol_mult": round(float(vol.iloc[peak_i]) / avg, 2) if avg > 0 else None}


def analyze_abc(df, cfg: dict = ABC_CONFIG) -> dict:
    """일봉 df → ABC 판정.

    반환 dict의 `verdict`:
      "ABC"        — A 충족(차트 후보). b_ok / c_stage가 함께 채워진다
      "다른 셋업"   — A 없음 + MA200 위 장기 → 박스/눌림. 탭에서 제외하되 카운트
      "ABC 아님"    — 그 외(데이터 부족 포함)
    """
    out = {"verdict": "ABC 아님", "reason": None, "a": None, "b": None,
           "c_stage": None, "ma200_pct": None, "vol_mult": None,
           "supply_above": False, "supply_bars": 0, "turnover_eok": None,
           "breakout": None, "close": None, "ma200": None}
    if df is None or getattr(df, "empty", True) or len(df) < cfg["min_bars"]:
        out["reason"] = f"봉 부족({0 if df is None else len(df)} < {cfg['min_bars']})"
        return out

    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"]
    last = float(close.iloc[-1])
    ma200 = _ma(close, 200)
    if not ma200:
        out["reason"] = "MA200 계산 불가"
        return out
    out["ma200_pct"] = round((last / ma200 - 1) * 100, 1)
    # ★ 추적 트리거로 쓰려면 비율이 아니라 **가격**이 필요하다(화면에서 재계산 금지).
    out["close"], out["ma200"] = last, round(ma200, 2)

    v_avg = float(vol.iloc[-cfg["vol_avg_bars"] - 1:-1].mean()) if len(vol) > cfg["vol_avg_bars"] else 0.0
    out["vol_mult"] = round(float(vol.iloc[-1]) / v_avg, 2) if v_avg > 0 else None
    out["turnover_eok"] = round(last * float(vol.iloc[-1]) / 1e8, 1)

    # ── A: 최근 250봉 고점 → 그 이후 저점 ──────────────────────────
    win_h = high.iloc[-cfg["a_lookback"]:]
    win_l = low.iloc[-cfg["a_lookback"]:]
    hi_pos = int(win_h.values.argmax())
    hi = float(win_h.iloc[hi_pos])
    after_l = win_l.iloc[hi_pos:]
    lo_rel = int(after_l.values.argmin())
    lo = float(after_l.iloc[lo_rel])
    drop = (hi - lo) / hi if hi > 0 else 0.0
    span = lo_rel                       # 고점→저점 봉수
    out["a"] = {"high": round(hi, 2), "low": round(lo, 2),
                "drop_pct": round(drop * 100, 1), "span_bars": span,
                "bars_since_low": len(win_l) - 1 - (hi_pos + lo_rel)}
    a_ok = drop >= cfg["a_drop_min"] and span >= cfg["a_span_min"]

    if not a_ok:
        # 다른 셋업: MA200 위에 오래 머문 종목 — 탭에서 빼되 카운트는 남긴다
        ma_series = close.rolling(200).mean()
        above = (close.iloc[-cfg["other_above_ma200_bars"]:]
                 > ma_series.iloc[-cfg["other_above_ma200_bars"]:]).sum()
        if int(above) >= cfg["other_above_ma200_bars"]:
            out["verdict"] = "다른 셋업"
            out["reason"] = "박스/눌림 (ABC 아님) — MA200 위 장기"
        else:
            out["reason"] = (f"A 미달 (하락 {drop*100:.0f}% / {span}봉, "
                             f"기준 {cfg['a_drop_min']*100:.0f}% · {cfg['a_span_min']}봉)")
        return out

    out["verdict"] = "ABC"

    # ── B: **저점 직후** 횡보 (사용자 확정 2026-09-18, 안 (b)) ──────
    # 저점 이후 **전 구간**으로 재면 −70% 빠졌다가 MA200까지 올라온 종목은
    # 범위가 60%대라 **C2에 도달한 종목이 B를 구조적으로 통과할 수 없었다**
    # (A급이 원리적으로 안 나옴). 그래서 저점 **직후**만 보고, 그 뒤 상승분은
    # B 판정에서 제외한다 — 상승은 C가 담당한다.
    # 20~60봉 중 **조건을 만족하는 가장 긴 구간**을 고른다.
    n_since = out["a"]["bars_since_low"]
    lo_idx = len(close) - 1 - n_since          # 저점 봉의 위치
    best = None
    for n in range(cfg["b_max_bars"], cfg["b_min_bars"] - 1, -1):
        if lo_idx + n > len(close):
            continue                            # 저점 직후로 n봉을 못 채움
        seg_h = high.iloc[lo_idx:lo_idx + n]
        seg_l = low.iloc[lo_idx:lo_idx + n]
        seg_c = close.iloc[lo_idx:lo_idx + n]
        lo_s = float(seg_l.min())
        if lo_s <= 0:
            continue
        rng = (float(seg_h.max()) - lo_s) / lo_s
        med = float(seg_c.median())
        cand = {"bars": n, "range_pct": round(rng * 100, 1),
                "median_vs_ma200_pct": round((med / ma200 - 1) * 100, 1),
                "ok": bool(rng <= cfg["b_range_max"]
                           and abs(med / ma200 - 1) <= cfg["b_ma200_band"])}
        if best is None:
            best = cand                         # 가장 긴 후보(조건 불문) — 표시용
        if cand["ok"]:
            best = cand                         # 조건 만족하는 가장 긴 구간
            break
    out["b"] = best or {"bars": n_since, "range_pct": None,
                        "median_vs_ma200_pct": None, "ok": False}

    # ── C: 단계 — **상태**로 본다 (사용자 확정 2026-09-18) ──────────
    # C2를 "당일 돌파"라는 **이벤트**로 두면 ① +5~20%인데 당일 돌파가 아닌
    # 상태가 어느 단계에도 안 들어가는 빈틈이 생기고 ② 돌파 다음날 탭에서
    # 사라진다. 그래서 위치(close vs MA200)로 단계를 정하고, 진돌이/가돌이는
    # **돌파봉의 거래량**으로 구분한 뒤 그 라벨을 유지한다.
    d = last / ma200 - 1
    out["breakout"] = _find_breakout(close, vol, ma200, cfg)
    c0, c1, c2 = cfg["c0"], cfg["c1"], cfg["c2"]
    if d >= cfg["c3_min"]:
        out["c_stage"] = "C3 이탈"
    elif c2[0] <= d < c2[1]:
        bo = out["breakout"]
        if bo is None:
            out["c_stage"] = "C2 돌파 없음"    # 60봉 내 돌파 없이 계속 위 — 등급엔 무관
        else:
            out["c_stage"] = ("C2 진돌이" if (bo["vol_mult"] or 0) >= cfg["c2_vol_mult"]
                              else "C2 가돌이")
    elif c1[0] <= d < c1[1]:
        out["c_stage"] = "C1 벽앞"
    elif c0[0] <= d < c0[1]:
        out["c_stage"] = "C0 대기"
    else:
        out["c_stage"] = None
        out["reason"] = f"C 구간 밖 (MA200 대비 {d*100:+.1f}%)"

    # ── 매물대: MA200 ~ MA200×1.3에 과거 250봉 중 몇 봉이 머물렀나 ──
    lo_b, hi_b = ma200 * cfg["supply_band"][0], ma200 * cfg["supply_band"][1]
    c_win = close.iloc[-cfg["a_lookback"]:]
    n_in = int(((c_win >= lo_b) & (c_win <= hi_b)).sum())
    out["supply_above"] = bool(n_in >= cfg["supply_min_bars"])
    out["supply_bars"] = n_in
    return out


def company_axis(turnover_eok: float | None, rev_yoy_pos: int | None,
                 eps_pos_q: int | None, major_holder_issue: bool,
                 cfg: dict = ABC_CONFIG, *, rev_yoy_of: int) -> dict:
    """기업 축 — 충족/미달 목록과 거래대금 미달 여부.

    `turnover_fail`은 v5.267에서 `trading_only`를 개명한 것이다 — 값의 의미가
    "트레이딩용 종목"이 아니라 **"거래대금 기준 미달"** 하나뿐인데 이름이 용도를
    말하고 있었다(CLAUDE.md "이름이 의미와 어긋나면 개명" 원칙). 등급 매트릭스가
    3단계로 바뀌면서 이 값은 C급 판정의 입력으로만 쓰인다.

    **시총은 보지 않는다**: 번들이 이미 시총 1000억 필터를 통과한 종목만
    담고 있어(app._fetch_market_data_inner가 fetch 이전에 자른다) 전원 충족이라
    변별력이 없다. 사용자 지시로 기준 자체는 남기되 판정에서 제외한다.
    """
    fails = []
    turnover_fail = turnover_eok is not None and turnover_eok < cfg["min_turnover_eok"]
    if turnover_fail:
        fails.append(f"거래대금 {turnover_eok:.0f}억 < {cfg['min_turnover_eok']}억")
    # naver 모바일이 분기를 6개만 줘서 YoY를 4분기 전부 볼 수 없는 경우가 많다.
    # **판정 가능한 분기 수(rev_yoy_of)가 기준에 못 미치면 감점하지 않는다** —
    # "판정 불가"를 "미달"로 뭉개면 없는 근거로 등급을 깎는 셈이다.
    if (rev_yoy_pos is not None and rev_yoy_of >= cfg["rev_yoy_min_quarters"]
            and rev_yoy_pos < cfg["rev_yoy_min_quarters"]):
        fails.append(f"매출 YoY+ {rev_yoy_pos}/{rev_yoy_of}분기")
    if eps_pos_q is not None and eps_pos_q < cfg["eps_positive_quarters"]:
        fails.append(f"EPS 흑자 {eps_pos_q}/{cfg['eps_positive_quarters']}분기")
    if major_holder_issue:
        fails.append("최대주주 이슈")
    return {"ok": not fails, "fails": fails, "turnover_fail": turnover_fail}


def grade(res: dict, comp: dict) -> str | None:
    """등급 3단계 (사용자 확정 2026-09-18).

        A급  = 차트 A·B·C 전부 & 기업 전부 충족
        B급  = 차트 전부 & 기업 감점  /  또는  기업 충족 & B 미달
        C급  = 거래대금 미달 또는 C3 이탈
        제외 = A 없음 (None)

    **판정 순서**: C급 조건을 먼저 본다 — 거래대금 미달·C3 이탈은 차트가
    아무리 좋아도 위로 못 올라가는 강등 조건이라서다.

    **"B 미달 **그리고** 기업 감점"** — 위 네 줄 어디에도 안 나오는 조합이라
    내가 C급으로 메웠고, 사용자가 확정했다: "둘 다 못 하면 최하위"
    (2026-09-18). 제외는 A가 없을 때만이다.
    """
    if res.get("verdict") != "ABC":
        return None
    stage = res.get("c_stage")
    if comp.get("turnover_fail") or stage == "C3 이탈":
        return "C급"
    # 여기부터 comp["ok"]는 거래대금을 뺀 나머지 기업 축의 통과 여부다
    # (거래대금 미달은 위에서 이미 C급으로 빠졌다).
    chart_ok = bool(res.get("b", {}).get("ok")) and stage is not None
    if chart_ok and comp.get("ok"):
        return "A급"
    if chart_ok or comp.get("ok"):
        return "B급"
    return "C급"
