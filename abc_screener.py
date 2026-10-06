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
    # ⓦ 와이코프 매집 7단계 대응(v5.331 기록): ①클라이맥스는 A 저점 "위치"만, ④박스는 B 폭(+v5.331 B 품질 표시),
    # ⑥박스 상단 돌파는 MA600 강돌파로 대신한다. **②자동반등·③2차테스트·⑤스프링·⑦되돌림은 의도적으로 모델링하지 않는다**
    # (사용자 결정 2026-10-06 "와이코프 ②③⑤⑦ 상태기계는 만들지 않는다 — 탭 목적은 ⑥ 강돌파 발견").
    # ── 두 기준선: 역할이 다르다 (v5.272, 사용자 지시) ──────────────
    # v5.268에서 MA600이 **모든** 판정을 맡았더니 13종목 중 11개가 C3 이탈로
    # 쏠렸다(MA600은 2.4년 평균이라 그간 오른 종목은 기준선이 한참 아래 남는다).
    # 그래서 역할을 쪼갠다:
    #   gate  MA600 — **후보/탈락만** 가른다. 위면 ABC 후보, 아래면 C0 대기
    #                 (장기 추세 미전환).
    #   stage MA200 — **그 안에서** C 단계·B 중앙값 밴드·매물대·돌파를 판정.
    # 이름에 역할을 박아 어느 선이 무엇을 하는지 값만 봐도 알게 한다.
    "gate_ma_period": 600,
    "stage_ma_period": 200,
    # A: 긴 하락
    "a_lookback": 250,        # 고점 탐색 구간(봉) ≈ 52주
    "a_drop_min": 0.40,       # 고점→저점 하락폭 ≥ 40%
    "a_span_min": 40,         # 고점→저점 소요 ≥ 40봉
    # B: 바닥 횡보
    "b_min_bars": 20,         # 저점 **직후** 횡보로 인정할 최소 봉수
    "b_max_bars": 60,         # 같은 구간의 최대 — 20~60 중 가장 긴 것을 고른다
    "b_range_max": 0.25,      # 그 구간 고저 범위 ≤ 25%
    "b_ma_band": 0.15,        # 구간 중앙값이 **MA200** ±15% 안(v5.267 기준 복귀)
    # ── C: 단계 — **MA600 기준** (v5.291, 사용자 지시로 전면 개편) ─────
    # 방법론 정정: ABC의 핵심은 "MA600을 거래량 동반 장대양봉으로 뚫느냐"다.
    # v5.272까지 C단계를 MA200으로 재던 것은 방법론과 불일치였다.
    # **판정 순서가 핵심 — 강돌파(이벤트)를 밴드(위치)보다 먼저 본다.**
    # 실측 근거(2026-09-27, 참조 12종목): 밴드를 먼저 보면 MA600이 2.4년
    # 평균이라 그간 오른 종목의 종가가 이미 +20%를 넘어 **10/12가 이탈로
    # 쏠리고 실제 강돌파(티이엠씨 +27.1%·vol×42, 우리넷 +12.2%·vol×26)가
    # 이탈에 먹혔다**. v5.268에서 겪은 것과 같은 현상이라 순서로 해결한다.
    # MA600 게이트는 제거했다 — 대기·벽앞이 화면에 보여야 한다(사용자 지시).
    "wall_band": -0.05,       # 벽앞 하한 — MA600 −5% ~ 0% (임의값)
    "exit_min": 0.20,         # 이탈 — MA600 +20% 이상 (임의값, 사용자 확정 유지)
    # 강돌파: 최근 N봉 안에 MA600 첫 종가 돌파 + 그 봉이 당일 +X% 양봉 +
    # 거래량 ≥ M배. 전부 **임의값**(백테스트 근거 없음, 사용자 지정 수치).
    "strong_window": 20,      # 최근 N봉 내 첫 돌파만 강돌파로 본다
    "strong_day_pct": 0.07,   # 돌파봉 당일 등락률 하한 (+7%)
    "strong_vol_mult": 2.0,   # 돌파봉 거래량 ÷ 평균 (사용자 확정: **50일 평균**)
    "c2_vol_mult": 3.0,       # 진돌이 기준 거래량 배수(돌파봉/직전 5일평균) — v5.331부터 _find_breakout이 실제로 쓴다
                              # (≥ 진돌이 / 미만 가돌이, 표시 전용 — 그 전엔 참조 0곳인 죽은 상수였다)
    "vol_avg_bars": 5,
    # ── 🩷 MA600 첫 상향돌파 이벤트 (v5.276, 사용자 지시) ──────────
    # 양봉맨 정의 A급 = **게이트선(MA600) 첫 상향돌파(종가) + 돌파 3봉 내
    # 거래량 ≥ 1.5×(50일 평균)**. C 단계와 **독립**이다 — C는 "지금 어디
    # 있나"(상태)이고 이건 "언제 넘었나"(사건)라, 같은 종목이 C3여도 최근
    # 돌파면 여기 잡힌다.
    "gate_break_window": 10,       # 최근 N봉 내 돌파만 표시(임의값)
    "gate_break_vol_bars": 3,      # 돌파봉 포함 N봉 중 최대 거래량(임의값)
    "gate_break_vol_mult": 1.5,    # 그 최대 거래량 ÷ 50일 평균 기준(임의값)
    "gate_break_vol_avg": 50,      # 기준 평균 일수(임의값)
    "breakout_lookback": 60,  # 돌파봉 탐지 창(봉) — 이 안에 돌파가 없으면 "돌파 없음"
    "breakout_vol_window": 3, # 돌파봉 포함 N봉 중 **최대 거래량**으로 진돌이/가돌이
                              # 판정(임의값). 첫 교차봉이 소량이고 다음날 대량이
                              # 터지는 형태가 흔해, 교차봉 하나만 보면 그걸
                              # "가돌이"로 잘못 부른다(LS에코 09-15 2.09배 →
                              # 09-16 13.01배).
    # 매물대 — v5.331(사용자 지시 "최근 250봉을 가격 10구간 볼륨 프로파일로 나눠, 현재가 위쪽에서 거래량 최대 구간을
    # '매물대 N~M원'으로 카드 표시. 기존 MA200×1.3 체류 계산 제거"). 창은 A와 같은 a_lookback(250봉)을 쓴다.
    # 예전 supply_band(MA200~×1.3)·supply_min_bars(30봉)는 가격대 매물이 아니라 "이동평균 근처 체류 봉 수"였다(삭제).
    "supply_profile_bins": 10,     # 가격 구간 수(사용자 지시 값). 표시 전용 — 등급·판정에 안 쓴다
    # 테마 동반(v5.334, 사용자 지시 "같은 테마 종목 중 당일 +5% 이상 오른 종목 수 / 테마 전체 종목 수") — 사용자 지시 값.
    # 표시 전용 — 등급·판정에 안 쓴다.
    "theme_up_pct": 5.0,
    # 다른 셋업(ABC 아님) 판정
    "other_above_ma_bars": 60,     # 게이트선 위 60봉 이상이면 박스/눌림
    # 기업 축
    # v5.271 수정(사용자 지시): 하한 300억 → **30억**. 실측에서 하한 300억이
    # 후보의 82%를 잘라내고 남은 A급이 POSCO홀딩스였다 — "소형 성장주"라는
    # 전제와 반대로 **하한이 대형주를 고르고 있었다**. 30억은 "B구간 횡보
    # 종목의 평소 수준"(사용자 지시)이며 **임의값**이다. 미달 → C급
    # (호가가 얇아 진입 자체가 어렵다).
    # v5.272 재조정: 30억 → **10억**(임의값). 나무가 6억·우리넷 7억은 여전히
    # C급이고 사용자가 그게 맞다고 확인했다 — "그 유동성이면 호가가 얇다".
    "min_turnover_eok": 10,
    # 거래대금 평균을 낼 B구간 봉 수(사용자 지시 "B구간 20봉 평균").
    "turnover_avg_bars": 20,
    # v5.271(사용자 지시): **상한**. "양봉맨 ABC는 소형 성장주"라 거래대금이
    # 너무 큰 대형주는 등급을 B로 막는다. 1,000억은 **임의값**(측정 근거 없음).
    # 처음 지시는 상한도 300억이었는데 그러면 하한과 같아져 **A급 가능 구간이
    # 정확히 300억 한 점**으로 사라진다 — 지적 후 1,000억으로 확정.
    #   < 10억       → C급(강등)
    #   10~1,000억   → A급 가능
    #   > 1,000억    → "대형", B 이하
    "max_turnover_eok": 1000,
    "rev_yoy_min_quarters": 3,     # 최근 4분기 중 매출 YoY+ 분기 수
    "rev_yoy_window": 4,
    "eps_positive_quarters": 2,    # 최근 2분기 EPS 흑자
    # 판정에 필요한 최소 봉수 — **기준선 기간과 같이 움직여야 한다.**
    # 250으로 두면 게이트선(MA600)이 NaN인 종목이 그대로 통과한다.
    # `_min_bars(cfg)`가 max(250, ma_period)로 계산한다(리터럴 금지).
    "min_bars_floor": 250,
}

# ── 단계 라벨 (v5.291, 사용자 지시) ─────────────────────────────────
# **리터럴 비교 금지 — 반드시 이 상수를 참조할 것.** v5.272까지 `grade()`가
# `stage == "C3 이탈"`로 문자열을 직접 비교했는데, 라벨을 바꾸면 그 비교가
# 조용히 항상 False가 되어 **이탈 종목이 C급 강등을 안 받고 A급까지 올라간다**
# (테스트 없으면 안 잡히는 회귀). 사용자 지시로 상수화했다.
STAGE_STRONG = "🩷 강돌파"     # MA600을 거래량 동반 장대양봉으로 뚫은 사건 — A급 유일 후보
STAGE_WALL = "벽앞"            # MA600 바로 아래
STAGE_WEAK = "약돌파"          # MA600 위지만 강돌파 조건 미달
STAGE_WAIT = "대기"            # MA600보다 한참 아래
STAGE_EXIT = "이탈"            # MA600 +exit_min 이상 — C급 강등

# 화면·정렬·우선순위가 쓰는 정렬된 순서(= 사용자 지시 우선순위).
C_STAGES = (STAGE_STRONG, STAGE_WALL, STAGE_WEAK, STAGE_WAIT, STAGE_EXIT)

# ── v5.331 표시 전용 라벨(등급·판정에 쓰지 않는다 — test_abc_display_only.py가 등급 분포 동일을 고정) ──
B_ABSORB = "흡수"            # B 후반부 종가 최저 > 전반부 종가 최저 & 후반 평균 거래량 < 전반
B_REDROP = "재하락 주의"      # B 후반부 종가 최저 < 전반부 종가 최저
B_NEUTRAL = "중립"
B_NONE = "B 미형성"           # 저점 직후 b_min_bars봉을 못 채움(B 구간 자체가 없다)
BREAKOUT_TRUE = "진돌이"      # MA200 돌파 거래량 배수 ≥ c2_vol_mult
BREAKOUT_FALSE = "가돌이"

# ── 등급 라벨 (v5.292) ───────────────────────────────────────────────
# `A급 보류` 신설: 🩷강돌파 + 기업축 통과인데 **기업축 판정에 쓸 실적이 없어**
# 그 "통과"를 신뢰할 수 없는 상태(사용자 지시). `company_axis()`의 매출·EPS
# 검사는 결측이면 건너뛰므로(v5.267 의도) 결측 종목은 `ok=True`가 되는데,
# v5.292에서 A급이 **기업축에만** 걸리게 되면서 그게 곧 "미조회면 A급"이 된다 —
# 실측으로 2배 과대(실적 결측 가정 23종목 vs 실조회 12종목)였다.
GRADE_A = "A급"
GRADE_A_PENDING = "A급 보류"
GRADE_B = "B급"
GRADE_C = "C급"
GRADES = (GRADE_A, GRADE_A_PENDING, GRADE_B, GRADE_C)


def _min_bars(cfg: dict = ABC_CONFIG) -> int:
    """판정 최소 봉수. 기준선 기간보다 짧으면 MA가 NaN이라 판정 자체가 불가."""
    return max(cfg["min_bars_floor"], cfg["gate_ma_period"])


def _ma_label(cfg: dict = ABC_CONFIG) -> str:
    """게이트선 라벨. 리터럴 "MA600"을 박으면 기간을 바꿔도 안 따라온다."""
    return f"MA{cfg['gate_ma_period']}"


def _ma(close, n: int):
    return float(close.iloc[-n:].mean()) if len(close) >= n else None


def _find_breakout(close, vol, cfg: dict):
    """최근 `breakout_lookback`봉 중 **MA200을 아래→위로 넘어간 첫 봉**.

    v5.272(사용자 지시): 기준을 게이트선(MA600)에서 **MA200**으로, 그리고
    마지막 교차 → **첫 교차**로. 진돌이/가돌이는 "그 돌파가 어떤 성격이었나"라
    처음 넘은 순간이 기준이다 — 뒤에 되밟고 다시 넘은 봉을 집으면 원래 돌파의
    거래량이 사라진다.

    없으면 None("돌파 없음" — 60봉 내내 위에 있었다는 뜻).
    vol_mult는 **그 돌파봉의** 거래량 ÷ 직전 5일평균이다(오늘 거래량이 아니다) —
    진돌이/가돌이 라벨은 돌파 시점의 성격이고 이후 유지된다(사용자 확정).

    ⚠️ 비교 기준은 **각 봉 시점의 기준선**(이동값)이다. 처음엔 오늘의 값
    하나를 상수로 두고 과거 봉을 비교했는데, 그러면 "오늘 기준선"을 며칠 전에
    넘은 봉이 돌파로 잡혀 **돌파 시점과 거래량이 둘 다 틀렸다**
    (LS에코 2026-09-18: 잘못된 값 vol 2.09 / 3봉 전).
    """
    n = len(close)
    look = min(cfg["breakout_lookback"], n - 1)
    ma_series = close.rolling(cfg["stage_ma_period"]).mean()
    found = None
    for i in range(n - look, n):
        if i < 1:
            continue
        m = ma_series.iloc[i]
        if m != m:                      # NaN — 기준선 기간 미만 구간
            continue
        if float(close.iloc[i - 1]) <= float(m) < float(close.iloc[i]):
            found = i
            break                       # v5.272: **첫** 상향돌파에서 멈춘다
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
    mult = round(float(vol.iloc[peak_i]) / avg, 2) if avg > 0 else None
    # v5.331: 진돌이/가돌이 라벨 복원(표시 전용) — 기존 c2_vol_mult(3.0)를 이 배수에 그대로 적용
    label = None if mult is None else (BREAKOUT_TRUE if mult >= cfg["c2_vol_mult"] else BREAKOUT_FALSE)
    return {"bars_ago": n - 1 - found,
            "vol_bar_ago": n - 1 - peak_i,
            "vol_mult": mult, "label": label}


def _find_gate_break(close, vol, cfg: dict, opn=None):
    """게이트선(MA600)을 **아래→위로 넘은 첫 봉**을 최근 `strong_window`봉 안에서
    찾고, 그 돌파가 **강돌파**인지 판정한다. 없으면 None.

    v5.291(사용자 지시) 개편 — 이 함수가 이제 C단계의 `🩷 강돌파`를 결정한다.
    예전엔 C단계와 독립된 "사건 표시" 전용이었는데, 방법론의 핵심이
    "MA600을 거래량 동반 장대양봉으로 뚫느냐"라 단계 자체가 이 사건이다.

    강돌파 3조건(전부 `ABC_CONFIG`, 전부 **임의값** — 백테스트 근거 없음):
      ① 최근 `strong_window`(20)봉 안에 MA600 첫 종가 돌파
      ② 그 돌파봉의 **당일 등락률** ≥ `strong_day_pct`(+7%)
      ③ 그 돌파봉의 **거래량 ÷ 직전 `gate_break_vol_avg`(50)일 평균**
         ≥ `strong_vol_mult`(2.0)
    ②의 등락률 기준은 **전봉 종가 대비**다(시가 대비가 아니다) — "장대양봉"을
    갭 포함 일간 상승률로 읽는다. `opn`을 넘기면 시가 대비도 같이 계산해
    참고로 내보내지만 판정에는 쓰지 않는다(기준을 둘로 만들면 어긋난다).

    평균 기준이 **50일**인 근거: 사용자 확정(2026-09-27). `_find_breakout`
    (MA200)은 직전 5일평균을 쓰는데 그쪽은 건드리지 않는다 — 다른 선·다른
    목적이고, 같은 이름(`vol_mult`)이라 혼동하기 쉬워 여기 명시한다.

    D+1/2/3 거래량 배수도 같은 50일 평균으로 함께 낸다(화면 표시용) — 돌파 후
    거래량이 유지되는지 사람이 보고 판단할 수 있게(판정에는 안 쓴다).
    """
    n = len(close)
    look = min(cfg["strong_window"], n - 1)
    if look < 1:
        return None
    ma = close.rolling(cfg["gate_ma_period"]).mean()
    found = None
    for i in range(n - look, n):
        if i < 1:
            continue
        m = ma.iloc[i]
        if m != m:
            continue
        if float(close.iloc[i - 1]) <= float(m) < float(close.iloc[i]):
            found = i
            break                      # **첫** 돌파
    if found is None:
        return None
    k = cfg["gate_break_vol_avg"]
    prev = vol.iloc[max(0, found - k):found]
    avg = float(prev.mean()) if len(prev) else 0.0
    bar_vol = float(vol.iloc[found])
    mult = round(bar_vol / avg, 2) if avg > 0 else None
    prev_c = float(close.iloc[found - 1])
    day_pct = round((float(close.iloc[found]) / prev_c - 1) * 100, 1) if prev_c else None
    open_pct = None
    if opn is not None:
        o = float(opn.iloc[found])
        open_pct = round((float(close.iloc[found]) / o - 1) * 100, 1) if o else None
    dplus = []
    for step in (1, 2, 3):
        j = found + step
        dplus.append(round(float(vol.iloc[j]) / avg, 2) if (j < n and avg > 0) else None)
    vol_ok = bool(mult is not None and mult >= cfg["strong_vol_mult"])
    day_ok = bool(day_pct is not None and day_pct >= cfg["strong_day_pct"] * 100)
    return {"bars_ago": n - 1 - found,
            "vol_mult": mult, "vol_ok": vol_ok,
            "day_pct": day_pct, "day_ok": day_ok, "open_pct": open_pct,
            "dplus": dplus,
            "strong": bool(vol_ok and day_ok)}


def b_quality(close, vol, lo_idx: int, b: dict | None) -> dict:
    """v5.331(사용자 지시) B 품질 — 찾은 B 구간을 전반부/후반부로 나눠 저점·거래량 추이. **표시 전용.**
    저점 = **종가 최저**(사용자 선택). 저가로 재면 B가 A 저점 봉에서 시작하고 A 저점은 그 뒤 전체 저가의 최저라
    후반부가 전반부보다 낮을 수 없어 "재하락 주의"가 구조상 안 나온다(2026-10-06 확인).
      재하락 주의 = 후반 종가 최저 < 전반 종가 최저
      흡수       = 후반 종가 최저 > 전반 종가 최저 그리고 후반 평균 거래량 < 전반 평균 거래량
      중립       = 그 외
      B 미형성   = B 구간이 없다(저점 직후 b_min_bars봉 미만 — 범위를 못 잼)
    홀수 봉이면 가운데 봉은 후반부에 넣는다(n//2 기준)."""
    if not b or b.get("range_pct") is None:
        return {"label": B_NONE}
    n = int(b["bars"])
    seg_c = close.iloc[lo_idx:lo_idx + n]
    seg_v = vol.iloc[lo_idx:lo_idx + n]
    h = n // 2
    c1, c2, v1, v2 = seg_c.iloc[:h], seg_c.iloc[h:], seg_v.iloc[:h], seg_v.iloc[h:]
    low1, low2 = float(c1.min()), float(c2.min())
    vol1, vol2 = float(v1.mean()), float(v2.mean())
    if low2 < low1:
        label = B_REDROP
    elif low2 > low1 and vol2 < vol1:
        label = B_ABSORB
    else:
        label = B_NEUTRAL
    return {"label": label, "low1": round(low1, 2), "low2": round(low2, 2),
            "vol1": round(vol1), "vol2": round(vol2), "bars": n}


def supply_profile(close, vol, last: float, cfg: dict = ABC_CONFIG) -> dict:
    """v5.331(사용자 지시) 매물대 — 최근 a_lookback(250)봉 종가 범위를 supply_profile_bins(10)개 같은 폭 구간으로
    나누고 각 봉 거래량을 그 봉 **종가**가 속한 구간에 더한다(일봉 종가×거래량 근사 — 평가 페이지 매물대와 같은 근사).
    "현재가 위쪽" = 구간 하단 ≥ 현재가인 구간. 그중 거래량 최대 구간이 매물대(거래량 0이면 없음). **표시 전용.**
    반환 bins는 구간별 거래량 전부(수동 대조용 — format_supply_bins)."""
    c = close.iloc[-cfg["a_lookback"]:]
    v = vol.iloc[-cfg["a_lookback"]:].fillna(0)
    lo, hi = float(c.min()), float(c.max())
    nb = cfg["supply_profile_bins"]
    if not (hi > lo) or len(c) == 0:
        return {"zone": None, "bins": []}
    w = (hi - lo) / nb
    idx = ((c - lo) / w).astype(int).clip(0, nb - 1)
    sums = v.groupby(idx.values).sum()
    total = float(v.sum())
    bins = [{"lo": round(lo + i * w, 2), "hi": round(lo + (i + 1) * w, 2), "vol": float(sums.get(i, 0.0))}
            for i in range(nb)]
    above = [b for b in bins if b["lo"] >= last and b["vol"] > 0]
    zone = None
    if above:
        z = max(above, key=lambda b: b["vol"])
        zone = {"lo": z["lo"], "hi": z["hi"], "vol_share_pct": round(z["vol"] / total * 100, 1) if total else None}
    return {"zone": zone, "bins": bins}


def format_supply_bins(prof: dict, last: float) -> list:
    """매물대 수동 대조용 — 구간별 거래량 한 줄씩(높은 가격부터). ▲ = 현재가 위 구간, ★ = 고른 매물대."""
    z = prof.get("zone") or {}
    total = sum(b["vol"] for b in prof.get("bins") or []) or 1
    out = []
    for b in reversed(prof.get("bins") or []):
        mark = ("★" if z and b["lo"] == z.get("lo") else " ") + ("▲" if b["lo"] >= last else " ")
        out.append(f"{mark} {b['lo']:>12,.0f} ~ {b['hi']:>12,.0f}  거래량 {b['vol']:>16,.0f}  ({b['vol'] / total * 100:5.1f}%)")
    return out


def day_change_pct(df) -> "float | None":
    """당일 등락률(%) = 마지막 봉 종가 ÷ 그 전 봉 종가 − 1. 봉이 2개 미만·전일 종가 0이면 None. (v5.334 테마 동반 — 표시 전용)"""
    if df is None or len(df) < 2:
        return None
    c = df["Close"]
    prev, last = float(c.iloc[-2]), float(c.iloc[-1])
    return round((last / prev - 1) * 100, 2) if prev > 0 else None


def theme_companions(themes: dict, changes: dict, cfg: dict = ABC_CONFIG) -> dict:
    """v5.334 테마 동반 — {테마명: [티커…]}와 {티커: 당일 등락률 %|None} → {티커: [{theme, up, total, no_data}]}.
    up = 그 테마 종목 중 당일 등락률 ≥ theme_up_pct(+5%)인 수(자기 자신 포함), total = 테마 종목 수 전체,
    no_data = 등락률을 모르는 종목 수(번들에 일봉 없음 — total엔 포함, up엔 안 셈). **표시 전용.**"""
    out = {}
    for name, members in (themes or {}).items():
        ms = list(dict.fromkeys(members))
        chg = [changes.get(m) for m in ms]
        up = sum(1 for c in chg if c is not None and c >= cfg["theme_up_pct"])
        info = {"theme": name, "up": up, "total": len(ms), "no_data": sum(1 for c in chg if c is None)}
        for m in ms:
            out.setdefault(m, []).append(info)
    return out


def analyze_abc(df, cfg: dict = ABC_CONFIG) -> dict:
    """일봉 df → ABC 판정.

    반환 dict의 `verdict`:
      "ABC"        — A 충족(차트 후보). b_ok / c_stage가 함께 채워진다
      "다른 셋업"   — A 없음 + 기준선 위 장기 → 박스/눌림. 탭에서 제외하되 카운트
      "MA600 불가"  — 봉이 기준선 기간보다 짧다. **등급 제외, 카운트만**
                      (사용자 지시 v5.268). 라벨은 `_ma_label()`로 만들어져
                      기간을 바꾸면 같이 바뀐다
      "ABC 아님"    — 그 외(데이터 부족 포함)
    """
    out = {"verdict": "ABC 아님", "reason": None, "a": None, "b": None,
           "c_stage": None, "gate_pct": None, "stage_pct": None, "vol_mult": None,
           "supply_zone": None, "b_quality": {"label": B_NONE},
           "b_turnover_eok": None, "turnover_today_eok": None,
           "breakout": None, "close": None,
           "ma_gate": None, "ma_stage": None,
           "gate_ma_period": cfg["gate_ma_period"],
           "stage_ma_period": cfg["stage_ma_period"], "ma_inverted": None,
           "gate_break": None}
    n_bars = 0 if df is None or getattr(df, "empty", True) else len(df)
    need = _min_bars(cfg)
    if n_bars < need:
        # v5.268(사용자 지시): 기준선을 못 그리는 종목은 **등급에서 빼고 세기만**
        # 한다. "봉 부족"과 한 덩어리로 묶으면 화면에서 몇 종목이 기준선 때문에
        # 빠졌는지 안 보인다.
        out["verdict"] = f"{_ma_label(cfg)} 불가"
        out["reason"] = f"{_ma_label(cfg)} 계산 불가 — {n_bars}봉 < {need}봉"
        return out

    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"]
    last = float(close.iloc[-1])
    ma_gate = _ma(close, cfg["gate_ma_period"])
    ma_stage = _ma(close, cfg["stage_ma_period"])
    if not ma_gate or not ma_stage:
        out["verdict"] = f"{_ma_label(cfg)} 불가"
        out["reason"] = f"{_ma_label(cfg)} 계산 불가"
        return out
    out["gate_pct"] = round((last / ma_gate - 1) * 100, 1)
    out["stage_pct"] = round((last / ma_stage - 1) * 100, 1)
    # ★ 추적 트리거로 쓰려면 비율이 아니라 **가격**이 필요하다(화면에서 재계산 금지).
    out["close"] = last
    out["ma_gate"], out["ma_stage"] = round(ma_gate, 2), round(ma_stage, 2)
    # v5.272: 역배열은 **표시 전용**이 됐다(사용자 지시 "등급 무관. 정보만").
    # v5.271에선 이게 C2 진돌이를 막았는데, 게이트/판정을 분리하면서 그 역할을
    # MA600 게이트가 가져갔다 — 같은 뜻을 두 곳에서 강제하면 어긋난다.
    out["ma_inverted"] = bool(ma_stage < ma_gate)

    v_avg = float(vol.iloc[-cfg["vol_avg_bars"] - 1:-1].mean()) if len(vol) > cfg["vol_avg_bars"] else 0.0
    out["vol_mult"] = round(float(vol.iloc[-1]) / v_avg, 2) if v_avg > 0 else None
    # **당일** 거래대금 — v5.271부터 판정에 안 쓴다(표시 전용). 급등 당일 값이라
    # B구간 횡보 종목의 "평소 유동성"을 왜곡한다는 게 기준을 바꾼 이유다.
    out["turnover_today_eok"] = round(last * float(vol.iloc[-1]) / 1e8, 1)

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
        # 다른 셋업: 기준선 위에 오래 머문 종목 — 탭에서 빼되 카운트는 남긴다
        ma_series = close.rolling(cfg["gate_ma_period"]).mean()
        above = (close.iloc[-cfg["other_above_ma_bars"]:]
                 > ma_series.iloc[-cfg["other_above_ma_bars"]:]).sum()
        if int(above) >= cfg["other_above_ma_bars"]:
            out["verdict"] = "다른 셋업"
            out["reason"] = f"박스/눌림 (ABC 아님) — {_ma_label(cfg)} 위 장기"
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
                "median_vs_ma_pct": round((med / ma_stage - 1) * 100, 1),
                "ok": bool(rng <= cfg["b_range_max"]
                           and abs(med / ma_stage - 1) <= cfg["b_ma_band"])}
        if best is None:
            best = cand                         # 가장 긴 후보(조건 불문) — 표시용
        if cand["ok"]:
            best = cand                         # 조건 만족하는 가장 긴 구간
            break
    out["b"] = best or {"bars": n_since, "range_pct": None,
                        "median_vs_ma_pct": None, "ok": False}
    out["b_quality"] = b_quality(close, vol, lo_idx, out["b"])   # v5.331 표시 전용

    # ── 거래대금: **B구간 앞 N봉 평균**(사용자 지시 v5.271) ──────────
    # 판정일 거래대금을 쓰면 돌파 당일 급등 값이 잡혀 "평소 유동성"이 아니다.
    # 저점 직후(= 바닥 다지기) 구간의 평균이 그 종목이 평소에 소화하는 양이다.
    # 봉이 모자라면 **추정하지 않고 None** — 없는 값을 0으로 두면 "거래대금
    # 미달"로 읽혀 조용히 C급이 된다.
    tb = cfg["turnover_avg_bars"]
    seg_c = close.iloc[lo_idx:lo_idx + tb]
    seg_v = vol.iloc[lo_idx:lo_idx + tb]
    out["b_turnover_eok"] = (round(float((seg_c * seg_v).mean()) / 1e8, 1)
                             if len(seg_c) >= tb else None)

    # ── C: 단계 — **MA600 기준, 이벤트 우선** (v5.291, 사용자 지시) ───
    # 판정 순서가 핵심이다. `🩷 강돌파`를 **밴드보다 먼저** 본다 — MA600은
    # 2.4년 평균이라 그간 오른 종목의 종가가 이미 +20%를 넘어, 밴드를 먼저
    # 보면 실제 강돌파가 "이탈"에 먹힌다(2026-09-27 실측: 참조 12종목 중
    # 10종목이 이탈로 쏠리고 티이엠씨 +27.1%·vol×42, 우리넷 +12.2%·vol×26
    # 두 건의 진짜 돌파가 가려졌다). v5.268에서 겪은 것과 같은 현상이다.
    #
    # `_find_breakout`(MA200)은 **그대로 둔다** — 진돌이/가돌이 성격 표시는
    # MA200 유지가 사용자 확정이고, 이제 C단계를 정하지 않는다(참고 칸).
    out["breakout"] = _find_breakout(close, vol, cfg)
    # MA600 첫 돌파 + 강돌파 판정. 이게 `🩷 강돌파` 단계를 결정한다.
    out["gate_break"] = _find_gate_break(close, vol, cfg, opn=df["Open"])
    gb = out["gate_break"]
    d = last / ma_gate - 1                 # ← 단계는 **MA600** 기준
    if gb is not None and gb["strong"] and last > ma_gate:
        # ① 이벤트 우선 — 밴드 무관. 단 "지금도 MA600 위"일 것(되밟았으면 아니다).
        out["c_stage"] = STAGE_STRONG
        out["reason"] = (f"{_ma_label(cfg)} 돌파 {gb['bars_ago']}봉 전 · "
                         f"당일 {gb['day_pct']:+.1f}% · 거래량 {gb['vol_mult']}배")
    elif d >= cfg["exit_min"]:
        out["c_stage"] = STAGE_EXIT
        out["reason"] = f"{_ma_label(cfg)} 대비 {d*100:+.1f}% (이탈 기준 +{cfg['exit_min']*100:.0f}%)"
    elif d >= 0:
        out["c_stage"] = STAGE_WEAK
        out["reason"] = (f"{_ma_label(cfg)} 위 {d*100:+.1f}% — 강돌파 조건 미달"
                         + ("" if gb is None else
                            f"(당일 {gb['day_pct']:+.1f}%/{cfg['strong_day_pct']*100:.0f}% · "
                            f"거래량 {gb['vol_mult']}/{cfg['strong_vol_mult']}배)"))
    elif d >= cfg["wall_band"]:
        out["c_stage"] = STAGE_WALL
        out["reason"] = f"{_ma_label(cfg)} 바로 아래 ({d*100:+.1f}%)"
    else:
        out["c_stage"] = STAGE_WAIT
        out["reason"] = f"{_ma_label(cfg)} 아래 대기 ({d*100:+.1f}%)"

    # ── 매물대(v5.331): 최근 250봉 가격 10구간 볼륨 프로파일, 현재가 위 최대 거래량 구간 — 표시 전용 ──
    out["supply_zone"] = supply_profile(close, vol, last, cfg)["zone"]
    return out


def company_axis(b_turnover_eok: float | None, rev_yoy_pos: int | None,
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
    turnover_fail = (b_turnover_eok is not None
                     and b_turnover_eok < cfg["min_turnover_eok"])
    if turnover_fail:
        fails.append(f"B구간 거래대금 {b_turnover_eok:.0f}억 < {cfg['min_turnover_eok']}억")
    # v5.271: 상한 초과는 **미달이 아니다** — fails에 넣으면 기업 축 탈락으로
    # 읽혀 C급까지 떨어진다. 지시는 "B 이하"라 별도 플래그로 내보내고
    # grade()가 상한선으로만 쓴다.
    turnover_large = (b_turnover_eok is not None
                      and b_turnover_eok > cfg["max_turnover_eok"])
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
    # v5.292(사용자 지시): 매출·EPS가 **둘 다 없으면** 기업축의 "통과"는 실적을
    # 안 본 결과다(위 두 검사가 `is not None` 가드로 건너뛰어졌다). `ok`만 보면
    # 미조회와 진짜 통과가 구분되지 않아 `grade()`가 A급을 준다 — 그래서
    # 사실을 별도 필드로 내보내고 등급 쪽에서 `A급 보류`로 막는다.
    # "조회했으나 데이터 없음"과 "미조회"는 여기선 같게 취급한다(둘 다 판정
    # 근거가 없다) — 그 구분은 `fin_reason`/화면 배지가 유지한다(v5.291).
    fin_unknown = rev_yoy_pos is None and eps_pos_q is None
    return {"ok": not fails, "fails": fails, "turnover_fail": turnover_fail,
            "turnover_large": turnover_large, "fin_unknown": fin_unknown}


def grade(res: dict, comp: dict) -> str | None:
    """등급 (v5.292, 사용자 지시 — 안4′).

        A급      = 🩷강돌파 & 기업축 통과 & 거래대금 하한·상한 안
        A급 보류 = 위 조건인데 **기업축 판정에 쓸 실적이 없음**(미조회/데이터 없음)
        B급      = 기업축 통과 & 강돌파 아님  (+ 강돌파지만 거래대금 상한 초과)
        C급      = 거래대금 미달 · 이탈 · 기업축 미달
        제외     = A 없음 (None)

    **`b.ok`(차트 B 구간)는 등급 계산에서 완전히 빠졌다.** 화면엔 참고 칸으로
    남는다. 근거(2026-09-27 실측, 유니버스 968 후보): B 중앙값이 MA200 ±15%
    안이어야 한다는 조건은 −40%+ 급락 후 39봉 바닥을 다진 종목과 원리적으로
    양립하지 않아(편차 중앙 −20%대) 통과가 4.5%뿐이었고, **A급이 0건**이었다.
    밴드를 MA600으로 옮기거나(3.9%) 부호 범위로 바꾸거나(5.9%) 아예 없애도(10.0%)
    A급은 ≤1건, 기준 MA를 "B구간 당시 값"으로 교정해도 4.0%/0건 — 즉 이 조건은
    A급을 막는 병목이었고 어떤 밴드로도 풀리지 않았다.
    `b.ok`를 B급 규칙에만 남기는 안(안4)도 재봤는데 "b.ok만 통과하고 기업축은
    미달"인 종목이 **유니버스에 0건**이라 결과가 완전히 동일했다 — 죽은 조건을
    남기지 않으려고 완전히 뺐다(사용자 확정).

    그 결과 **B급 정의가 "기업축 통과 + 강돌파 아님" 한 갈래로 단순해진다**
    (현행은 "차트 전부·기업 감점" / "기업 충족·B 미달" / "강돌파지만 대형" 세
    갈래가 섞여 있었다). 거래대금 상한 초과 천장은 규칙으로는 남지만 실측
    강돌파 44종목 중 해당 0건이다.

    ⚠️ 단계·등급 비교는 **반드시 상수**(`STAGE_*`/`GRADE_*`)로. 리터럴을 쓰면
    라벨을 바꿀 때 비교가 조용히 항상 False가 된다
    (`test_abc_ma600_stages.py::test_grade_uses_stage_constants_not_literals`).

    **판정 순서**: C급 강등 조건을 먼저 본다 — 거래대금 미달·이탈은 위로 못
    올라가는 조건이라서다.
    """
    if res.get("verdict") != "ABC":
        return None
    stage = res.get("c_stage")
    if comp.get("turnover_fail") or stage == STAGE_EXIT:
        return GRADE_C
    if comp.get("ok") and stage == STAGE_STRONG:
        # 거래대금 상한 초과("대형")는 **A를 막는 천장**이지 미달이 아니다(v5.271).
        if comp.get("turnover_large"):
            return GRADE_B
        # v5.292: 실적을 못 봤으면 A급을 주지 않는다 — 위 `ok`가 실적 검사를
        # 건너뛴 결과일 수 있다. 보류는 "아직 모른다"이고 B급 강등이 아니다.
        if comp.get("fin_unknown"):
            return GRADE_A_PENDING
        return GRADE_A
    if comp.get("ok"):
        return GRADE_B
    return GRADE_C


if __name__ == "__main__":   # 매물대 수동 대조: python3 abc_screener.py 365590.KQ
    import sys
    import naver_kr
    from app import _downcast
    tk = sys.argv[1]
    df = _downcast(naver_kr.fetch(tk))
    last = float(df["Close"].iloc[-1])
    prof = supply_profile(df["Close"], df["Volume"], last)
    print(f"{tk} 현재가 {last:,.0f} · 최근 {ABC_CONFIG['a_lookback']}봉 · 매물대 {prof['zone']}")
    print("\n".join(format_supply_bins(prof, last)))
