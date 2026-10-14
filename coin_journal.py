"""코인 일지(바이낸스 USDT-M 선물 연습 기록) — 검증·시각 정규화·계산 (v5.347, 사용자 지시 2026-10-14).

주식 일지(진입/대기/종료/무산)와 별개, 구조도 다르다. 한 건 = 한 사이클:
  1차 진입 → (추가 진입 1·2, 선택) → (덜어내기, 선택) → (종료, 선택).
app.py 미의존 — 저장·rev·삭제 로그는 app.py의 공용 레코드 저장(_rev_store_put 등)이 맡고, 여기는 순수 계산만.

계산(사용자 지시 원문 기준):
- 평단 = 수량 가중: Σ(증거금×배율) ÷ Σ(증거금×배율 ÷ 진입가) — 바이낸스 표시 평단과 같다.
- 건별 손익: 롱 (청산가−진입가)/진입가 × 배율 × 증거금, 숏은 부호 반대.
  추가분(추가 1·2)의 청산가 = 덜어낸 가격(없으면 종료가), 1차 청산가 = 종료가.
  **단순화(사용자 지시)**: 덜어내기는 추가 1·2 전량을 같은 가격에 청산한 것으로 본다(추가분을 따로 나눠 덜어내는 경우는 기록하지 않는다).
- ROI % = 건별 손익 합 ÷ 증거금 합. 수수료 차감 ROI = 건별로 증거금×배율×FEE_ROUNDTRIP을 빼고 같은 식.
- 종료가가 없으면 ROI·손익 미표시(덜어내기만 있어도 미표시). 경과 = 1차 진입 → 종료.
상태(자동): 종료가 있으면 "종료", 아니면 덜어낸 가격이 있으면 "덜어냄·보유중", 아니면 "진행중".
시각: 저장은 UTC 오프셋 포함 ISO. 입력이 오프셋 없는 "YYYY-MM-DDTHH:MM"이면 뉴질랜드 현지 시각(Pacific/Auckland, 서머타임 반영)으로 본다.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

NZ = ZoneInfo("Pacific/Auckland")
SYMBOL_DEFAULT = "BTCUSDT"
TIMEFRAMES = ("5m", "15m", "30m")              # "봉 주기 5m/15m/30m(기본 5m)"
SIDES = ("long", "short")                       # 롱/숏(기본 롱)
LEVERAGE_DEFAULT = 10
LEVERAGE_MAX = 125                              # 바이낸스 USDT-M 최대 배율(입력 상한 — AI 판단)
EXIT_REASONS = ("목표도달", "포기선", "강제청산", "충동", "기타")   # 사용자 지시 원문
FEE_ROUNDTRIP = 0.0012                          # "증거금×배율×0.0012(왕복, 상수)" — 사용자 지시
REVIEW_MAX = 200                                # "한 줄 복기" 입력 상한(AI 판단)
TEXT_MAX = 100
STATUS_OPEN, STATUS_TRIMMED, STATUS_CLOSED = "진행중", "덜어냄·보유중", "종료"
ADD_KEYS = ("add1", "add2")


def _num(v):
    try:
        x = float(v)
        return x if x == x and x not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def normalize_time(v) -> "str | None":
    """ISO(오프셋·Z 포함)는 그대로 오프셋 ISO로, 오프셋 없는 값은 뉴질랜드 현지 시각으로 해석. 빈 값 None. 해석 불가면 ValueError."""
    if v in (None, ""):
        return None
    s = str(v).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"시각 형식이 아님: {s!r}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NZ)
    return dt.isoformat(timespec="seconds")


def now_iso() -> str:
    return datetime.now(NZ).isoformat(timespec="seconds")


def status_of(rec: dict) -> str:
    if rec.get("exit"):
        return STATUS_CLOSED
    if rec.get("trim"):
        return STATUS_TRIMMED
    return STATUS_OPEN


def normalize(rec: dict, creating: bool = False) -> dict:
    """입력 레코드 정리: 기본값·대문자·시각 정규화·빈 구간 None·상태 계산. 검증은 invalid()가 따로."""
    out = dict(rec)
    out["symbol"] = (str(rec.get("symbol") or SYMBOL_DEFAULT)).strip().upper()
    out["tf"] = rec.get("tf") or "5m"
    out["side"] = rec.get("side") or "long"
    out["leverage"] = _num(rec.get("leverage")) if rec.get("leverage") not in (None, "") else LEVERAGE_DEFAULT
    e = dict(rec.get("entry") or {})
    e["time"] = normalize_time(e.get("time")) or (now_iso() if creating else None)
    out["entry"] = e
    for k in ADD_KEYS + ("trim", "exit"):
        leg = rec.get(k)
        if not leg or _num((leg or {}).get("price")) is None and not (leg or {}).get("margin"):
            out[k] = None
            continue
        leg = dict(leg)
        leg["time"] = normalize_time(leg.get("time"))
        out[k] = leg
    t = rec.get("target") or {}
    out["target"] = {"text": str(t.get("text") or "").strip(), "price": _num(t.get("price"))}
    out["exit_reason"] = rec.get("exit_reason") or None
    out["review"] = str(rec.get("review") or "").strip()
    out["status"] = status_of(out)
    return out


def invalid(rec: dict) -> "str | None":
    """저장 거부 사유(문자열) 또는 None. normalize() 뒤의 레코드를 받는다."""
    if not rec.get("symbol") or len(rec["symbol"]) > 20:
        return "심볼이 필요해요"
    if rec.get("tf") not in TIMEFRAMES:
        return f"봉 주기는 {'/'.join(TIMEFRAMES)} 중 하나"
    if rec.get("side") not in SIDES:
        return "방향은 long/short"
    lev = _num(rec.get("leverage"))
    if lev is None or not (0 < lev <= LEVERAGE_MAX):
        return f"배율은 0 초과 {LEVERAGE_MAX} 이하"
    e = rec.get("entry") or {}
    if not ((_num(e.get("price")) or 0) > 0 and (_num(e.get("margin")) or 0) > 0):
        return "1차 진입가·증거금은 0보다 커야 해요"
    if not e.get("time"):
        return "1차 진입 시각이 필요해요"
    for k, label in (("add1", "추가 진입 1"), ("add2", "추가 진입 2")):
        leg = rec.get(k)
        if leg and not ((_num(leg.get("price")) or 0) > 0 and (_num(leg.get("margin")) or 0) > 0):
            return f"{label}: 가격·증거금은 0보다 커야 해요"
    if rec.get("add2") and not rec.get("add1"):
        return "추가 진입 2는 추가 진입 1이 있어야 해요"
    if rec.get("trim"):
        if not (rec.get("add1") or rec.get("add2")):
            return "추가 진입이 없는데 덜어낸 가격이 있어요 — 덜어내기는 추가 진입분 청산이에요"
        if not (_num(rec["trim"].get("price")) or 0) > 0:
            return "덜어낸 가격은 0보다 커야 해요"
    if rec.get("exit"):
        if not (_num(rec["exit"].get("price")) or 0) > 0:
            return "종료가는 0보다 커야 해요"
        if rec.get("exit_reason") not in EXIT_REASONS:
            return f"종료 사유가 필요해요({'/'.join(EXIT_REASONS)})"
    elif rec.get("exit_reason"):
        return "종료가 없는데 종료 사유가 있어요"
    tp = rec.get("target", {}).get("price")
    if tp is not None and tp <= 0:
        return "목표 가격은 0보다 커야 해요"
    if len(rec.get("review") or "") > REVIEW_MAX or len(rec.get("target", {}).get("text") or "") > TEXT_MAX:
        return "복기·목표 문구가 너무 길어요"
    return None


def legs(rec: dict) -> list:
    """진입 건 목록 [(이름, 진입가, 증거금, 청산가|None)]. 추가분 청산가 = 덜어낸 가격(없으면 종료가), 1차 청산가 = 종료가.
    덜어내기는 추가 1·2 전량에 같은 가격(사용자 지시 단순화)."""
    exit_px = _num((rec.get("exit") or {}).get("price"))
    trim_px = _num((rec.get("trim") or {}).get("price"))
    e = rec["entry"]
    out = [("entry", _num(e["price"]), _num(e["margin"]), exit_px)]
    for k in ADD_KEYS:
        leg = rec.get(k)
        if leg:
            out.append((k, _num(leg["price"]), _num(leg["margin"]), trim_px if trim_px is not None else exit_px))
    return out


def calc(rec: dict) -> dict:
    lev = _num(rec["leverage"])
    sign = 1 if rec.get("side") == "long" else -1
    ls = legs(rec)
    notional = sum(m * lev for _, _, m, _ in ls)
    qty = sum(m * lev / p for _, p, m, _ in ls)
    out = {"avg_entry": notional / qty, "margin_total": sum(m for _, _, m, _ in ls), "notional_total": notional,
           "n_adds": sum(1 for k in ADD_KEYS if rec.get(k)), "status": status_of(rec)}
    if not rec.get("exit"):
        out.update({"pnl": None, "roi_pct": None, "roi_net_pct": None, "duration_min": None})
        return out
    pnl = net = 0.0
    per = []
    for name, p, m, px in ls:
        g = sign * (px - p) / p * lev * m
        fee = m * lev * FEE_ROUNDTRIP
        pnl += g
        net += g - fee
        per.append({"leg": name, "entry": p, "margin": m, "close": px, "pnl": g, "fee": fee})
    mt = out["margin_total"]
    dur = None
    try:
        dur = (datetime.fromisoformat(rec["exit"]["time"]) - datetime.fromisoformat(rec["entry"]["time"])).total_seconds() / 60 \
            if rec["exit"].get("time") and rec["entry"].get("time") else None
    except (TypeError, ValueError):
        dur = None
    out.update({"pnl": pnl, "pnl_net": net, "roi_pct": pnl / mt * 100, "roi_net_pct": net / mt * 100, "legs": per, "duration_min": dur})
    return out


def stats(records: list) -> dict:
    """통계(종료 건만): 건수, 종료 사유별, 평균 ROI, 수수료 차감 평균 ROI, 추가 진입 있음/없음 평균 ROI와 n. MAE는 계산하지 않음."""
    closed = [(r, calc(r)) for r in records if r.get("exit")]

    def avg(xs):
        xs = [x for x in xs if x is not None]
        return sum(xs) / len(xs) if xs else None
    with_add = [c for r, c in closed if c["n_adds"] > 0]
    no_add = [c for r, c in closed if c["n_adds"] == 0]
    return {"n_closed": len(closed),
            "by_reason": {k: sum(1 for r, _ in closed if r.get("exit_reason") == k) for k in EXIT_REASONS},
            "avg_roi_pct": avg([c["roi_pct"] for _, c in closed]),
            "avg_roi_net_pct": avg([c["roi_net_pct"] for _, c in closed]),
            "with_adds": {"n": len(with_add), "avg_roi_pct": avg([c["roi_pct"] for c in with_add])},
            "no_adds": {"n": len(no_add), "avg_roi_pct": avg([c["roi_pct"] for c in no_add])}}
