"""시나리오 카드 — 재량 훈련용 레벨/시나리오 계산 (v5.196, 사용자 지시).

표시 전용, 새 지표 없음 — 이미 계산되는 값(신호 스냅샷 pivot/stop, 20일
고가, 21EMA/50MA/200MA, 베이스 저점)만 조합한다. 측정으로 검증된 전략이
아니라 "매매 전에 세 갈래를 미리 정해두고 그대로 따른다"는 사전 결정
훈련 도구다 — 예측 주장이 아니므로 호출부는 항상 label="재량 시나리오"로
표시해야 한다(게이트·판정 로직에 절대 연결하지 않음).

레벨 정의(사용자 스펙 그대로):
  저항 = 신호 스냅샷 pivot, 없으면 20일 고가
  지지 = 21EMA/50MA 중 현재가 "아래"에 있으면서 더 가까운(=더 높은) 값
         — 둘 다 현재가 위면 지지 없음(None)
  무효 = 200MA와 신호 스냅샷 stop 중 더 낮은 값, 둘 다 없으면 베이스 저점
"""
from __future__ import annotations


def compute_levels(close_series, high_series, low_series, pivot: float | None = None,
                    stop: float | None = None) -> dict:
    """close_series/high_series/low_series: pandas Series, 오름차순(마지막이
    최신). 각 레벨의 출처(source)도 같이 반환 — 카드 표시에 "135,700(21EMA)"
    처럼 붙이기 위함. 베이스 저점(무효 최종 폴백)은 200MA·스냅샷 stop이
    둘 다 없을 때만 최근 60봉 저가로 계산(scanner.py 여러 곳의 base_low
    관례 — lo.tail(N).min() — 와 같은 방식, N=60은 이 용도의 범용 기본값)."""
    close = float(close_series.iloc[-1])

    if pivot:
        resistance, resistance_source = float(pivot), "pivot"
    else:
        resistance, resistance_source = float(high_series.tail(20).max()), "20일고가"

    ema21 = float(close_series.ewm(span=21, adjust=False).mean().iloc[-1])
    ma50 = float(close_series.tail(50).mean()) if len(close_series) >= 50 else None
    below = []
    if ema21 < close:
        below.append((ema21, "21EMA"))
    if ma50 is not None and ma50 < close:
        below.append((ma50, "50MA"))
    if below:
        support, support_source = max(below, key=lambda x: x[0])
    else:
        support, support_source = None, None

    ma200 = float(close_series.tail(200).mean()) if len(close_series) >= 200 else None
    cands = []
    if ma200 is not None:
        cands.append((ma200, "200MA"))
    if stop:
        cands.append((float(stop), "손절가"))
    if cands:
        invalidation, invalidation_source = min(cands, key=lambda x: x[0])
    elif low_series is not None and len(low_series) > 0:
        invalidation = float(low_series.tail(60).min())
        invalidation_source = "베이스저점"
    else:
        invalidation, invalidation_source = None, None

    return {
        "close": round(close, 4),
        "resistance": round(resistance, 4), "resistance_source": resistance_source,
        "support": round(support, 4) if support is not None else None, "support_source": support_source,
        "invalidation": round(invalidation, 4) if invalidation is not None else None,
        "invalidation_source": invalidation_source,
    }


def compute_scenarios(levels: dict) -> dict:
    """세 갈래 + R비교. 레벨이 없으면(지지/무효 None) 해당 시나리오는 부분만
    채우거나 아예 None — 프론트가 "지지 없음이라 계산 불가" 식으로 표시."""
    close = levels["close"]
    resistance = levels["resistance"]
    support = levels.get("support")
    invalidation = levels.get("invalidation")

    risk1 = None
    if support is not None and resistance:
        risk1 = round((resistance - support) / resistance * 100, 1)
    scenario1 = {
        "label": "① 저항 돌파", "condition": "거래량 1.5배+ 동반 돌파",
        "entry": resistance, "stop": support, "risk_pct": risk1,
    }

    scenario2 = None
    if support is not None and invalidation is not None and support > invalidation:
        risk2 = round((support - invalidation) / support * 100, 1) if support else None
        r2 = round((resistance - support) / (support - invalidation), 2)
        scenario2 = {
            "label": "② 저항 거절 → 지지 조정", "condition": "저항에서 밀려 지지까지 되돌림",
            "entry": support, "stop": invalidation, "risk_pct": risk2, "r_multiple": r2,
        }

    scenario3 = {
        "label": "③ 지지 이탈 → 무효 터치",
        "condition": f"{'지지' if support is not None else '저항'} 깨고 무효까지 하락",
        "action": "관심 해제",
    }

    favored = None
    if scenario1.get("risk_pct") is not None and scenario2 and scenario2.get("risk_pct") is not None:
        favored = 1 if scenario1["risk_pct"] <= scenario2["risk_pct"] else 2

    highlight = None
    if resistance and close >= resistance * 0.98:
        highlight = 1
    elif support and close <= support * 1.02:
        highlight = 2

    return {
        "label": "재량 시나리오",
        "levels": levels,
        "scenario1": scenario1, "scenario2": scenario2, "scenario3": scenario3,
        "favored": favored, "highlight": highlight,
    }


def _fmt_price(v, market: str) -> str:
    if v is None:
        return "-"
    return f"{v:,.0f}" if market == "KR" else f"{v:,.2f}"


def memo_text(sc: dict | None, market: str) -> str:
    """v5.196 [4]: 저널 메모 세 줄 프리필 — "① 저항 돌파 시 진입 / ② 지지
    눌림 시 진입 / ③ 무효 이탈 시 해제". static/index.html의 scenarioMemoText()
    와 동일 포맷(서버에서 생성해야 하는 ⚡감시 경로용 — 클라이언트 fmtPrice와
    표시가 다르면 혼란스러우므로 같은 규칙: KR 정수 콤마, US 소수 2자리)."""
    if not sc or not sc.get("levels"):
        return ""
    lv = sc["levels"]
    r = _fmt_price(lv.get("resistance"), market)
    s = _fmt_price(lv.get("support"), market) if lv.get("support") is not None else "지지없음"
    i = _fmt_price(lv.get("invalidation"), market) if lv.get("invalidation") is not None else "무효기준없음"
    return f"① {r} 돌파 시 진입 / ② {s} 눌림 시 진입 / ③ {i} 이탈 시 해제"


def build_scenario(close_series, high_series, low_series, pivot: float | None = None,
                    stop: float | None = None) -> dict | None:
    """카드에 붙일 최종 시나리오 dict. 최소 요건(20봉 이상 — 20일 고가 계산
    가능)조차 안 되면 None(카드에서 시나리오 토글 자체를 생략)."""
    if close_series is None or len(close_series) < 20:
        return None
    levels = compute_levels(close_series, high_series, low_series, pivot, stop)
    return compute_scenarios(levels)
