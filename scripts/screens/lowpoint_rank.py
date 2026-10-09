"""저점 순위(v5.341) — 저점 코호트 분류 학습: 예측(분류) → 결과 → 비교를 기록·채점하는 순수 계산.

사용자 지시 요지: "사용자가 저점 종목을 고르는 '눈'을 키우는 학습 시스템. 예측(분류) → 결과 → 비교를 기록·채점한다. 대상은 매주
주봉 스캔 히트와 매월 월봉 스캔 히트(관찰 코호트 그대로). 관심 신호 학습용이며 측정 결론이 아님. 새 판정 임계값 금지."

레코드(서버 /data/lowpoint_rankings.json, 저장 규칙은 app.py — 저점 매매 기록과 같은 레코드 단위 rev·삭제 로그·날짜별 사본):
  id          r_{tf}_{label} — 관찰 코호트(주·월봉 기준봉 라벨) 하나에 한 건
  tf·label    관찰 레코드와 같은 값 · scan_at = 코호트가 스캔으로 등록된 시각
  deadline    스캔 실행일(scan_at의 KST 날짜) **다음** KR 거래일 09:00 KST — 이후 확정 불가(사후 편향 방지). v5.341 수정(사용자
              지시 "라벨 기준이 아니라 스캔 실행일의 다음 KR 거래일 09:00 KST") — 월봉은 1일 08:00 스캔이라 라벨(월말) 기준이면 1일
              09:00에 닫혀 1시간뿐이었다. 주봉(토요일 스캔)은 라벨 기준과 같은 날이 나온다.
  items       [{watch_id, code, name, mkt, base_date, base_price}] — 관찰 레코드 그대로(확정 전까지 새 히트를 합친다)
  picks       {watch_id: {pick: first|normal|no, reasons: [칩 0~2개]}} — 없는 종목은 보통·칩 없음
  confirmed_at  확정 시각(이후 picks·snapshot 수정 불가) · snapshot {watch_id: 지표} — 확정 시점 값, 표시·리뷰 전용
  results     {watch_id: {anchor: {date, close}, d5|d10|d20: {date, close, pct}}} — 매일 07:00 추적이 확정 종가로 채운다(분류와
              무관 — 미분류도). 기준점 = **기한 시각에 확정돼 있던 마지막 봉의 종가**(lowpoint_watch.confirmed_through(시장, 기한) —
              KR 20:10 KST·US 뉴욕 16:00, 새 상수 없음), D+N = 그 다음 거래일부터 N번째 확정 봉. 스냅샷의 기준가 대비 %는 그대로(표시용).

채점: 확정 안 된 코호트(기한 지남)는 전 종목 "미분류". 출발 여부·출발일·단계는 관찰 레코드를 그대로 참조한다(여기서 판정 안 함).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pandas as pd

import lowpoint_eval as ev
import lowpoint_watch as w

KST = timezone(timedelta(hours=9))
# 사용자 지시 값(화면 문구·선택지 — 판정 임계값 아님)
PICKS = ("first", "normal", "no")
PICK_LABEL = {"first": "먼저 간다", "normal": "보통", "no": "안 간다", "unclassified": "미분류"}
DEFAULT_PICK = "normal"
REASONS = ("거래량 폭발 이력", "박스 상단 근접", "위가 비어 있음", "테마", "수급", "이평 수렴", "그냥 느낌")
MAX_REASONS = 2                        # "이유 칩 0~2개"
FIRST_GUIDE = (3, 7)                   # "'먼저 간다'는 3~7개 제한(화면 안내만, 저장은 막지 않음)"
CONFIRM_OPEN_HM = (9, 0)               # "코호트 기준일 다음 거래일 09:00 KST 이후에는 확정 불가"
HORIZONS = {"week": (5, 10), "month": (20,)}   # "주봉 D+5·D+10, 월봉 D+20의 종가 수익률"
MIN_COHORTS = {"week": 4, "month": 3}  # 상단 고지 "코호트 4주(월봉은 3개월) 쌓이기 전에는 결론 내지 않음"


def rank_id(tf: str, label: str) -> str:
    return f"r_{tf}_{label}"


def deadline(scan_day: str, is_trading_day) -> str:
    """스캔 실행일(KST 날짜) **다음** KR 거래일 09:00 KST — ISO 문자열. is_trading_day(market, 'YYYY-MM-DD') → bool."""
    d = date.fromisoformat(scan_day)
    for _ in range(15):
        d += timedelta(days=1)
        if is_trading_day("kr", d.isoformat()):
            break
    return datetime.combine(d, time(*CONFIRM_OPEN_HM), tzinfo=KST).isoformat()


def is_open(rec: dict, now_iso: str) -> bool:
    """확정 가능한가 — 아직 확정 안 했고 기한 전(기한 시각 정각부터 불가)."""
    return not rec.get("confirmed_at") and datetime.fromisoformat(now_iso) < datetime.fromisoformat(rec["deadline"])


def cohort_items(watch_records: list, tf: str, label: str) -> list:
    out = []
    for r in watch_records or []:
        if r.get("tf") == tf and r.get("label") == label:
            out.append({"watch_id": r["id"], "code": r["code"], "name": r.get("name") or r["code"], "mkt": r.get("mkt"),
                        "base_date": r["base_date"], "base_price": float(r["base_price"])})
    return sorted(out, key=lambda x: x["code"])


def new_record(tf: str, label: str, items: list, is_trading_day, now_iso: str, scan_at: str) -> dict:
    scan_day = datetime.fromisoformat(scan_at).astimezone(KST).date().isoformat()
    return {"id": rank_id(tf, label), "tf": tf, "label": label, "scan_at": scan_at, "deadline": deadline(scan_day, is_trading_day),
            "items": items, "picks": {}, "confirmed_at": None, "snapshot": {}, "results": {}, "created_at": now_iso}


def merge_items(rec: dict, items: list) -> bool:
    """확정 전이면 관찰에 새로 들어온 같은 코호트 종목을 합친다(있던 종목·순서는 그대로). 바뀌었으면 True."""
    if rec.get("confirmed_at"):
        return False
    have = {i["watch_id"] for i in rec.get("items") or []}
    add = [i for i in items if i["watch_id"] not in have]
    if add:
        rec["items"] = sorted((rec.get("items") or []) + add, key=lambda x: x["code"])
    return bool(add)


def validate_picks(picks, items: list) -> "str | None":
    if not isinstance(picks, dict):
        return "picks는 {관찰 id: {pick, reasons}}"
    ids = {i["watch_id"] for i in items}
    for wid, p in picks.items():
        if wid not in ids:
            return f"코호트에 없는 종목: {wid}"
        if not isinstance(p, dict) or p.get("pick") not in PICKS:
            return f"{wid}: pick은 {'|'.join(PICKS)}"
        rs = p.get("reasons") or []
        if not isinstance(rs, list) or len(rs) > MAX_REASONS or len(set(rs)) != len(rs) or any(x not in REASONS for x in rs):
            return f"{wid}: 이유 칩은 목록에서 0~{MAX_REASONS}개"
    return None


def clean_picks(picks: dict) -> dict:
    return {wid: {"pick": p["pick"], "reasons": list(p.get("reasons") or [])} for wid, p in (picks or {}).items()}


def pick_of(rec: dict, wid: str) -> str:
    """채점용 분류 — 확정 안 된 코호트는 전부 미분류, 확정이면 고른 값(안 고른 종목은 기본값 보통)."""
    if not rec.get("confirmed_at"):
        return "unclassified"
    return ((rec.get("picks") or {}).get(wid) or {}).get("pick") or DEFAULT_PICK


# ── 확정 시점 스냅샷(표시·리뷰 전용 — 어떤 판정도 이 값을 읽지 않는다) ──────────────────────────────
def max_vol_mult(daily: "pd.DataFrame | None", bars: int, avg_n: int) -> "float | None":
    """최근 bars봉 각각의 거래량 ÷ 그 봉 직전 avg_n거래일 평균 — 그중 최대. 직전 avg_n봉이 다 있는 봉만 센다(짧은 평균으로
    대신하지 않는다). 셀 봉이 없으면 None. bars·avg_n = abc_screener.ABC_CONFIG b_max_bars(60)·gate_break_vol_avg(50) 재사용."""
    if daily is None or daily.empty or "Volume" not in daily.columns:
        return None
    v = daily["Volume"].astype(float).reset_index(drop=True)
    best = None
    for i in range(max(0, len(v) - bars), len(v)):
        if i < avg_n:
            continue
        avg = float(v.iloc[i - avg_n:i].mean())
        if avg > 0:
            m = float(v.iloc[i]) / avg
            best = m if best is None or m > best else best
    return best


def snapshot_item(item: dict, daily: "pd.DataFrame | None", through: "str | None", watch_rec: "dict | None",
                  themes: "list | None", flow: "dict | None", vol_bars: int, vol_avg: int) -> dict:
    """확정 시점 지표 — 종가(확정 봉)·기준가 대비 %·ATR%·최근 vol_bars봉 최대 거래량 배수·관찰 단계·무효선까지 거리·테마·
    기관/외국인 5일 순매수 일수. 값을 못 구하면 None(0으로 뭉개지 않는다)."""
    d = w._confirmed_daily(daily, through)
    d = d.dropna(subset=["Close"]) if d is not None and not d.empty else d
    out = {"close": None, "close_date": None, "pct_vs_base": None, "atr_pct": None, "max_vol_mult": None,
           "stage": None, "invalid_dist_pct": None, "themes": themes or [], "flow": flow,
           "setup_type": "unknown", "setup_class": "unknown", "setup_b_quality": None}
    if d is not None and not d.empty:
        c = float(d["Close"].iloc[-1])
        out.update(close=c, close_date=str(pd.Timestamp(d.index[-1]).date()),
                   pct_vs_base=round((c / item["base_price"] - 1) * 100, 2),
                   atr_pct=ev.short_metrics(d)["atr_pct"], max_vol_mult=max_vol_mult(d, vol_bars, vol_avg))
    if watch_rec:
        st = watch_rec.get("stage") or ("watch" if watch_rec.get("status") == "active" else None)
        out["stage"] = st
        # v5.343 유형(바닥형/눌림형/판정 불가)·B 품질 — 확정 시점 관찰 레코드 값(07:00 추적이 ABC 판정으로 갱신)
        out["setup_type"] = watch_rec.get("setup_type") or "unknown"
        out["setup_class"] = watch_rec.get("setup_class") or ("bottom" if out["setup_type"] == "bottom" else "unknown")   # v5.345
        out["setup_b_quality"] = watch_rec.get("setup_b_quality")
        inv = watch_rec.get("invalid_line")
        if inv and out["close"] is not None and watch_rec.get("status") == "reached":
            out["invalid_dist_pct"] = round((out["close"] / float(inv) - 1) * 100, 2)
    return out


# ── 결과(확정 종가만) ────────────────────────────────────────────────────────────────────
def returns_for(item: dict, daily: "pd.DataFrame | None", through: "str | None", horizons,
                anchor_through: "str | None") -> dict:
    """기준점 = anchor_through(기한 시각의 확정일)까지의 마지막 봉 종가. 그 다음 거래일부터 센 N번째 **확정** 봉(through까지 —
    휴장일은 봉이 없으니 저절로 건너뜀)의 종가 수익률. 아직 N봉이 안 됐으면 그 키는 없다.
    반환 {"anchor": {"date", "close"}, "d5": {"date", "close", "pct"}, …} — 기준점 봉이 없으면 {}."""
    out = {}
    d = w._confirmed_daily(daily, through)
    if d is None or d.empty or anchor_through is None:
        return out
    d = d.dropna(subset=["Close"])
    upto = d[d.index <= pd.Timestamp(anchor_through)]
    if upto.empty:
        return out
    a_ts, a_close = upto.index[-1], float(upto["Close"].iloc[-1])
    out["anchor"] = {"date": str(a_ts.date()), "close": a_close}
    after = d[d.index > a_ts]
    for n in horizons:
        if len(after) >= n:
            c = float(after["Close"].iloc[n - 1])
            out[f"d{n}"] = {"date": str(after.index[n - 1].date()), "close": c, "pct": round((c / a_close - 1) * 100, 2)}
    return out


def results_done(rec: dict) -> bool:
    keys = [f"d{n}" for n in HORIZONS[rec["tf"]]]
    res = rec.get("results") or {}
    return all(all(k in (res.get(i["watch_id"]) or {}) for k in keys) for i in rec.get("items") or [])


# ── 리뷰(채점) ────────────────────────────────────────────────────────────────────────
def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


def review_rows(recs: list, watch_by_id: dict) -> list:
    """종목 행 — 분류(미분류 포함)·이유·스냅샷·결과·관찰 참조(출발 여부·출발일·단계)."""
    rows = []
    for rec in recs or []:
        for it in rec.get("items") or []:
            wid = it["watch_id"]
            wr = watch_by_id.get(wid) or {}
            p = (rec.get("picks") or {}).get(wid) or {}
            rows.append({"rank_id": rec["id"], "tf": rec["tf"], "label": rec["label"], "watch_id": wid,
                         "code": it["code"], "name": it["name"], "mkt": it["mkt"],
                         "pick": pick_of(rec, wid), "reasons": list(p.get("reasons") or []) if rec.get("confirmed_at") else [],
                         "snapshot": (rec.get("snapshot") or {}).get(wid), "results": (rec.get("results") or {}).get(wid) or {},
                         # v5.343 유형 — 확정 스냅샷 값이 우선(그 시점 판정), 없으면(미분류) 관찰 레코드 현재 값
                         "setup_type": (((rec.get("snapshot") or {}).get(wid) or {}).get("setup_type")
                                        or wr.get("setup_type") or "unknown"),
                         "setup_class": _row_class((rec.get("snapshot") or {}).get(wid), wr),   # v5.345
                         "departed": wr.get("status") == "reached", "departed_date": wr.get("reached_date"),
                         "stage": wr.get("stage") or ("watch" if wr.get("status") == "active" else None),
                         "watch_missing": not wr})
    return rows


SNAP_METRICS = ("pct_vs_base", "atr_pct", "max_vol_mult", "invalid_dist_pct")


SETUP_TYPES = ("bottom", "pullback", "unknown")
SETUP_CLASSES = ("bottom", "trend", "down", "unknown")   # v5.345 리뷰 유형 — 눌림형을 추세 눌림/하락 추세로 나눔


def _row_class(snap: "dict | None", wr: dict) -> str:
    """리뷰 분류(바닥형/추세 눌림/하락 추세/판정 불가) — 확정 스냅샷이 있으면 그 시점 값, 없으면(미분류) 관찰 현재 값.
    분류 필드가 없는 옛 값(v5.343~v5.344)은 바닥형이면 바닥형, 그 외는 판정 불가로 센다."""
    src = snap if snap else wr
    return _review_class(src.get("setup_class")) or ("bottom" if src.get("setup_type") == "bottom" else "unknown")


def _review_class(c: "str | None") -> "str | None":
    """리뷰 분류 키 — pullback_unknown은 판정 불가로 합친다."""
    if c is None:
        return None
    return c if c in SETUP_CLASSES else "unknown"


def review(recs: list, watch_by_id: dict) -> dict:
    rows = review_rows(recs, watch_by_id)
    keys = sorted({f"d{n}" for hs in HORIZONS.values() for n in hs}, key=lambda k: int(k[1:]))
    out = _review_parts(rows, keys)
    # ④ 이유 칩별 — 칩을 고른 종목 중 출발 비율
    chips = {}
    for c in REASONS:
        rs = [r for r in rows if c in r["reasons"]]
        chips[c] = {"n": len(rs), "departed": sum(1 for r in rs if r["departed"]),
                    "rate": round(sum(1 for r in rs if r["departed"]) / len(rs) * 100, 1) if rs else None}
    return {
        "cohorts": {tf: sum(1 for r in recs or [] if r["tf"] == tf) for tf in HORIZONS},
        "min_cohorts": MIN_COHORTS, **out,
        # v5.343 리뷰 ①② 유형별(바닥형/눌림형/판정 불가) — 같은 집계를 유형 부분집합에
        "by_type": {t: _review_parts([r for r in rows if r["setup_class"] == t], keys) for t in SETUP_CLASSES},
        "missed": [r for r in rows if r["pick"] in ("normal", "no") and r["departed"]],
        "chips": chips,
        "horizon_keys": keys,
    }


def _review_parts(rows: list, keys: list) -> dict:
    """리뷰 ①(분류별 성적·전체)·②(오른 종목 vs 안 오른 종목) — 행 부분집합 하나에 대해."""

    def agg(rs):
        return {"n": len(rs), "departed": sum(1 for r in rs if r["departed"]),
                "departed_rate": round(sum(1 for r in rs if r["departed"]) / len(rs) * 100, 1) if rs else None,
                "avg": {k: _avg([(r["results"].get(k) or {}).get("pct") for r in rs]) for k in keys},
                "n_ret": {k: sum(1 for r in rs if k in r["results"]) for k in keys}}
    by_pick = {p: agg([r for r in rows if r["pick"] == p]) for p in ("first", "normal", "no", "unclassified")}
    # ② 오른 종목 vs 안 오른 종목(출발 여부) — 스냅샷이 있는(확정된) 종목만
    snap = [r for r in rows if r["snapshot"]]

    def snap_avg(rs):
        return {"n": len(rs), **{m: _avg([r["snapshot"].get(m) for r in rs]) for m in SNAP_METRICS},
                "flow_organ": _avg([(r["snapshot"].get("flow") or {}).get("organ") for r in rs]),
                "flow_foreign": _avg([(r["snapshot"].get("flow") or {}).get("foreign") for r in rs]),
                "theme_share": round(sum(1 for r in rs if r["snapshot"].get("themes")) / len(rs) * 100, 1) if rs else None}
    return {"by_pick": by_pick, "total": agg(rows),
            "rose_vs_not": {"departed": snap_avg([r for r in snap if r["departed"]]),
                            "not_departed": snap_avg([r for r in snap if not r["departed"]]),
                            "no_snapshot": len(rows) - len(snap)}}
