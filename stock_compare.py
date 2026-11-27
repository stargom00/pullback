"""두 종목 비교 점검표(v5.404) — 순수 계산. 표시 전용 · 측정 전.

사용자 2026-11-27: 바이넥스·아남전자를 위치·바닥 모양·매물대·주봉 RSI·손익비·거래대금(평균·중앙값)·투자자별 순매수로 비교한 뒤
"지금 이 전체 항목들을 체크할수있는 페이지 붙여줘. 두 종목 비교로하자. 티커나 종목명 넣으면 볼수있게."

항목은 그 비교에서 쓴 것 그대로다. 새 판정 문턱은 없다 — 손절 제안의 1.5 ATR만 기존 스캐너 ATR×1.5(v4.67 stop_wide)를 빌렸다.
매물대는 abc_screener.supply_profile(ABC 탭과 같은 함수), RSI는 lowpoint.rsi_wilder_sma(고전 Wilder), 꿈틀이·다이버전스는 저점 관찰 칩과
같은 함수(사본 없음). "어느 쪽이 낫다"는 판정은 하지 않는다 — 숫자를 나란히 놓을 뿐.
내부 요구 봉 수: 600일선 600봉(모자라면 그 칸만 None), 나머지는 250봉 이하.
"""
from __future__ import annotations

import math
import os
import statistics
import sys

import pandas as pd

import investor_flow  # 먼저 — 뒤의 모듈 중 scripts/measurements를 sys.path 앞에 넣는 것이 있어 같은 이름의 측정용 investor_flow가 가린다
import abc_screener

_SCREENS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "screens")
if _SCREENS not in sys.path:
    sys.path.append(_SCREENS)          # 뒤에 붙인다 — 앞에 넣으면 같은 이름 모듈이 루트 것을 가릴 수 있다
import divergence  # noqa: E402
import lowpoint as lp  # noqa: E402
import stir_screen  # noqa: E402

MA_LIST = (20, 60, 120, 240, 600)


def _f(x, nd=4):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else round(x, nd)


def atr_wilder(df: pd.DataFrame, n: int = 14) -> "float | None":
    h, l, c = (df[k].astype(float).to_numpy() for k in ("High", "Low", "Close"))
    if len(c) < n + 1:
        return None
    tr = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    a = sum(tr[:n]) / n
    for x in tr[n:]:
        a = (a * (n - 1) + x) / n
    return a


def price_block(df: pd.DataFrame) -> dict:
    """확정·정제된 일봉(마지막 = 기준일) → 위치·이평·바닥·변동성·거래대금·매물대·RSI·칩."""
    c, v = df["Close"].astype(float), df["Volume"].astype(float)
    last = float(c.iloc[-1])
    out = {"date": str(pd.Timestamp(df.index[-1]).date()), "close": last, "bars": len(df)}
    hi = lambda w: float(df["High"].iloc[-w:].max())
    lo = lambda w: float(df["Low"].iloc[-w:].min())
    out["pos"] = {str(w): {"high": hi(w), "low": lo(w), "from_high": _f(last / hi(w) - 1), "from_low": _f(last / lo(w) - 1),
                           "high_date": str(pd.Timestamp(df["High"].iloc[-w:].idxmax()).date()), "bars": min(w, len(df))}
                  for w in (250, 750)}
    mas = {}
    for n in MA_LIST:
        if len(c) >= n + 20:
            m = c.rolling(n).mean()
            mas[str(n)] = {"ma": _f(m.iloc[-1], 2), "gap": _f(last / m.iloc[-1] - 1), "slope20": _f(m.iloc[-1] / m.iloc[-21] - 1)}
        else:
            mas[str(n)] = None
    out["ma"] = mas
    rng = lambda w: _f(float(c.iloc[-w:].max()) / float(c.iloc[-w:].min()) - 1) if len(c) >= w else None
    out["base"] = {"range60": rng(60), "range120": rng(120), "range8": rng(8),
                   "low8": float(df["Low"].iloc[-8:].min()), "high8": float(df["High"].iloc[-8:].max())}
    a = atr_wilder(df)
    out["atr"] = {"atr": _f(a, 2), "atr_pct": _f(a / last) if a else None, "stop_1_5": _f(last - 1.5 * a, 2) if a else None}
    val = (c * v) / 1e8                                       # 억 원
    out["turnover"] = {"avg20": _f(val.iloc[-20:].mean(), 2), "median20": _f(val.iloc[-20:].median(), 2),
                       "avg120": _f(val.iloc[-120:].mean(), 2), "last5": [_f(x, 2) for x in val.iloc[-5:]],
                       "vol20_120": _f(v.iloc[-20:].mean() / v.iloc[-120:].mean()) if v.iloc[-120:].mean() > 0 else None}
    ret = lambda k: _f(last / float(c.iloc[-1 - k]) - 1) if len(c) > k else None
    out["ret"] = {"5": ret(5), "20": ret(20), "60": ret(60)}
    sp = abc_screener.supply_profile(c, v, last)
    z = sp.get("zone")
    if z:
        avg_bin = sum(b["vol"] for b in sp["bins"]) / max(1, sum(1 for b in sp["bins"] if b["vol"] > 0))
        zb = next((b for b in sp["bins"] if b["lo"] == z["lo"]), None)
        z = {**z, "x_avg_bin": _f(zb["vol"] / avg_bin, 2) if zb and avg_bin else None, "dist": _f(z["lo"] / last - 1)}
    out["supply"] = z
    tot = sum(b["vol"] for b in sp.get("bins") or []) or 1
    out["supply_above"] = [{"lo": b["lo"], "hi": b["hi"], "share_pct": _f(b["vol"] / tot * 100, 1)}
                           for b in sp.get("bins") or [] if b["lo"] >= last]      # 현재가 위 구간 전부(가까운 것부터)
    wk = lp.resample_bars(c, "week")["close"]
    mo = lp.resample_bars(c, "month")["close"]
    rs = lambda s, k: [_f(x, 1) for x in lp.rsi_wilder_sma(s).iloc[-k:]] if len(s) > lp.RSI_PERIOD + 1 else []
    out["rsi"] = {"day": _f(lp.rsi_wilder_sma(c).iloc[-1], 1), "week4": rs(wk, 4), "month3": rs(mo, 3)}
    st = stir_screen.stir_check(df)
    out["chips"] = {"stir": st["stir"], "stir_parts": st["stir_parts"],
                    **divergence.div_fields(df, out["date"])}
    m = df.resample("ME").agg({"High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).iloc[-12:]
    out["monthly"] = [{"m": str(i.date())[:7], "high": float(r.High), "low": float(r.Low), "close": float(r.Close), "vol": float(r.Volume)}
                      for i, r in m.iterrows()]
    return out


def parse_flow_rows(raw: "list | None") -> list:
    """naver_kr.fetch_investor_trend 원본 줄 → [{date, foreign, organ, individual(주), close, volume, foreign_hold_pct}].
    숫자는 investor_flow._num(사본 없음) — 못 읽은 값은 그 줄을 버린다(0으로 뭉개지 않는다)."""
    iv = investor_flow
    assert hasattr(iv, "_num"), f"잘못된 investor_flow 모듈: {iv.__file__}"
    out = []
    for x in raw or []:
        d = str(x.get("bizdate") or "")
        vals = {k: iv._num(x.get(f)) for k, f in (("foreign", "foreignerPureBuyQuant"), ("organ", "organPureBuyQuant"),
                                                 ("individual", "individualPureBuyQuant"), ("close", "closePrice"),
                                                 ("volume", "accumulatedTradingVolume"))}
        if len(d) != 8 or any(v is None for v in vals.values()):
            continue
        hold = iv._num(str(x.get("foreignerHoldRatio") or "").rstrip("%"))
        out.append({"date": f"{d[:4]}-{d[4:6]}-{d[6:]}", **vals, "foreign_hold_pct": hold})
    return sorted(out, key=lambda r: r["date"])


def flow_block(rows: list) -> "dict | None":
    """parse_flow_rows 결과 → 기간 합계(억)·순매수 일수·거래량 대비·최근 10일(주)·외국인 보유율 처음/끝."""
    if not rows:
        return None
    vol = sum(r["volume"] for r in rows) or None
    blk = {}
    for k in ("foreign", "organ", "individual"):
        blk[k] = {"eok": _f(sum(r[k] * r["close"] for r in rows) / 1e8, 2), "buy_days": sum(r[k] > 0 for r in rows),
                  "share_of_volume": _f(sum(r[k] for r in rows) / vol) if vol else None,
                  "last10": [int(r[k]) for r in rows[-10:]]}
    hr = [r["foreign_hold_pct"] for r in rows if r.get("foreign_hold_pct") is not None]
    return {"days": len(rows), "from": rows[0]["date"], "to": rows[-1]["date"], **blk,
            "foreign_hold": {"first": hr[0], "last": hr[-1]} if hr else None}


def plan_block(px: "float | None", stop: "float | None", target: "float | None", atr: "float | None") -> "dict | None":
    """사용자가 넣은 매수가·손절·목표 → 손절 %·몇 ATR·목표 %·손익비(목표폭 ÷ 손절폭)."""
    if not px or px <= 0:
        return None
    o = {"px": px}
    if stop and stop < px:
        o["stop_pct"] = _f(stop / px - 1)
        o["stop_atr"] = _f((px - stop) / atr, 2) if atr else None
    if target and target > px:
        o["target_pct"] = _f(target / px - 1)
    if o.get("stop_pct") and o.get("target_pct"):
        o["rr"] = _f(o["target_pct"] / -o["stop_pct"], 2)
    return o


def median(xs):
    return statistics.median(xs) if xs else None
