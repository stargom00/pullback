"""섹터 합성지표 (v5.195, 사용자 지시 — 섹터 층 1단계 [3];
v5.201, 사용자 지시 — KR/US 시장별 분리).

스캔이 이미 메모리에 갖고 있는 종목별 일봉(OHLCV)만으로 섹터 단위 지표를
계산한다 — 추가 네트워크 fetch 0건. 섹터 소속은 호출부(app.py)가 넘겨주는
sector_of 콜백(=_sector_of, sectors.py/kr_sectors_auto.py/us_industry_
cache.py 병합 우선순위가 이미 반영된 값)을 그대로 쓴다 — 이 모듈은 순환
import 없이 순수 계산만 담당.

구성종목 5개 미만인 섹터는 통계적으로 무의미해 계산 대상에서 제외
(사용자 스펙 "구성 ≥5").

v5.201: 같은 "섹터" 이름이라도 시장(KR/US)별로 완전히 독립적인 그룹으로
취급한다 — AI 밸류체인 큐레이션 버킷(반도체-장비 등)은 원래 KR+US를
섞어서 하나로 잡는데, 그 상태로 섹터RS 백분위를 전체(KR+US 합쳐서
60여개 섹터) 안에서 매기면 US 섹터 수가 훨씬 많아(그리고 개별 종목도
많아) KR 섹터가 상위권에 낄 확률이 구조적으로 낮아진다("섹터 흐름"
카드에 KR이 안 보이던 원인). 그래서 sector_snapshot 내부적으로는 모든
(섹터, 시장) 조합을 별개의 그룹으로 만들어(구성원 국적이 섞인 섹터는
KR쪽/US쪽으로 쪼갬) 그 시장 안에서만 백분위를 매긴다 — by_sector/저널
키는 "섹터명|시장"(예: "반도체-장비|KR")으로 저장, 화면엔 섹터명만 보이되
"KR 상위5"/"US 상위5" 두 블록으로 나눠 보여주는 걸 전제로 한다
(app.py _build_sector_flow, static/index.html renderSectorFlowHtml 참고).

동일가중 누적지수는 날짜 인덱스로 정렬하지 않고 "종목별 최근 N개 봉"을
위치 기준으로 맞춘다 — 이제 그룹 자체가 시장별로 쪼개져 있어 이 이슈는
더 줄었지만(같은 시장은 휴장일이 대체로 같음), 단순함을 위해 그대로 유지.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

import pandas as pd

from scanner import to_rs_rank

MIN_SECTOR_SIZE = 5
_SNAPSHOT_FILENAME = "sector_snapshot.json"


def _dir() -> str:
    return os.environ.get("JOURNAL_DIR") or ("/data" if os.path.isdir("/data") else os.path.dirname(__file__))


def snapshot_path() -> str:
    return os.path.join(_dir(), _SNAPSHOT_FILENAME)


def _market_of(ticker: str) -> str:
    return "KR" if ticker.endswith((".KS", ".KQ")) else "US"


def _key(sector: str, market: str) -> str:
    return f"{sector}|{market}"


def _equal_weight_index(closes: dict) -> "pd.Series | None":
    """{ticker: Close Series} → 동일가중 누적지수(시작=100), 최근 min_len개 봉
    기준. min_len<61이면 60일 수익률을 못 구하므로 None."""
    if len(closes) < MIN_SECTOR_SIZE:
        return None
    min_len = min(len(s) for s in closes.values() if s is not None)
    if min_len < 61:
        return None
    aligned = pd.DataFrame({t: s.tail(min_len).reset_index(drop=True) for t, s in closes.items()})
    normed = aligned / aligned.iloc[0]
    return (normed.mean(axis=1) * 100.0).reset_index(drop=True)


def compute(data: dict, rs_ranks: dict, sector_of) -> dict:
    """data: {ticker: DataFrame(OHLCV)}, rs_ranks: {ticker: int(12개월 RS 백분위)},
    sector_of: ticker->섹터명 콜백.
    반환: {"by_ticker": {t: {sector, market, rank, total, sector_rs_pct}},
           "by_sector": {"섹터명|시장": {sector, market, n, ret20, ret60,
                                          sector_rs_pct, new_high_52w,
                                          pct_20d_high, pct_above_ma50, leaders}}}
    같은 섹터명이라도 KR/US는 항상 별개 그룹 — sector_rs_pct는 그 시장
    안에서만(v5.201) 매겨진다."""
    groups: dict[str, list] = defaultdict(list)
    for t in data:
        sec = sector_of(t)
        if sec and sec != "기타":
            groups[_key(sec, _market_of(t))].append(t)

    by_sector: dict[str, dict] = {}
    rank_maps: dict[str, dict] = {}
    ret60_by_key: dict[str, dict[str, float]] = {"KR": {}, "US": {}}

    for key, tickers in groups.items():
        if len(tickers) < MIN_SECTOR_SIZE:
            continue
        sec, mkt = key.rsplit("|", 1)
        closes = {t: data[t]["Close"] for t in tickers if data[t] is not None and "Close" in data[t]}
        idx = _equal_weight_index(closes)

        n_20d_high = n_above_ma50 = n_counted = 0
        for t in tickers:
            df = data.get(t)
            if df is None or "Close" not in df:
                continue
            c = df["Close"]
            if len(c) < 50:
                continue
            n_counted += 1
            if c.iloc[-1] >= c.tail(20).max():
                n_20d_high += 1
            if c.iloc[-1] > c.tail(50).mean():
                n_above_ma50 += 1

        entry = {
            "sector": sec, "market": mkt, "n": len(tickers),
            "ret20": None, "ret60": None, "new_high_52w": None,
            "pct_20d_high": round(n_20d_high / n_counted * 100, 1) if n_counted else None,
            "pct_above_ma50": round(n_above_ma50 / n_counted * 100, 1) if n_counted else None,
        }
        if idx is not None:
            if len(idx) >= 21:
                entry["ret20"] = round(float(idx.iloc[-1] / idx.iloc[-21] - 1) * 100, 2)
            entry["ret60"] = round(float(idx.iloc[-1] / idx.iloc[-61] - 1) * 100, 2)
            lookback = idx.tail(min(len(idx), 252))
            entry["new_high_52w"] = bool(idx.iloc[-1] >= lookback.max())
            ret60_by_key[mkt][key] = entry["ret60"]

        ranked = sorted(tickers, key=lambda tt: rs_ranks.get(tt, -1), reverse=True)
        rank_maps[key] = {tt: i + 1 for i, tt in enumerate(ranked)}
        entry["leaders"] = ranked[:3]
        by_sector[key] = entry

    for mkt in ("KR", "US"):
        pct_map = to_rs_rank(ret60_by_key[mkt]) if ret60_by_key[mkt] else {}
        for key, pct in pct_map.items():
            by_sector[key]["sector_rs_pct"] = pct
    for entry in by_sector.values():
        entry.setdefault("sector_rs_pct", None)

    by_ticker = {}
    for key, rmap in rank_maps.items():
        entry = by_sector[key]
        n, pct = entry["n"], entry.get("sector_rs_pct")
        for t, rank in rmap.items():
            by_ticker[t] = {"sector": entry["sector"], "market": entry["market"],
                             "rank": rank, "total": n, "sector_rs_pct": pct}

    return {"by_ticker": by_ticker, "by_sector": by_sector}


# ── 일별 영속 저널 (/data/sector_snapshot.json) ─────────────────────
def load_all() -> dict:
    try:
        with open(snapshot_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    tmp = snapshot_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, snapshot_path())


def save_market_stats(daykey: str, by_sector: dict) -> None:
    """시장 파생 통계(지수 수익률/52주신고가/신고가비율/MA50비율/섹터RS/대장)를
    해당 날짜 키에 기록. by_sector의 키는 "섹터명|시장"(compute() 참고).
    hits(5탭 히트)는 record_hits()가 같은 엔트리에 별도 누적하므로
    덮어쓰지 않게 보존."""
    all_data = load_all()
    day = all_data.setdefault(daykey, {})
    for key, entry in by_sector.items():
        rec = day.setdefault(key, {})
        rec.update({
            "sector": entry["sector"], "market": entry["market"],
            "n": entry["n"], "ret20": entry.get("ret20"), "ret60": entry.get("ret60"),
            "sector_rs_pct": entry.get("sector_rs_pct"),
            "new_high_52w": entry.get("new_high_52w"),
            "pct_20d_high": entry.get("pct_20d_high"),
            "pct_above_ma50": entry.get("pct_above_ma50"),
            "leaders": entry.get("leaders"),
        })
        rec.setdefault("hits", {})
    _save(all_data)


def record_hits(daykey: str, tab: str, tickers: list, sector_of) -> None:
    """탭 스캔 결과(히트 종목 리스트)를 (섹터,시장) 별로 집계해 오늘자
    엔트리에 기록. 같은 탭이 캐시로 재실행돼도 중복 카운트되지 않게, 탭
    이름을 키로 그날의 히트 집합을 통째로 교체(증분 누적 아님)."""
    if not tickers:
        by_sec: dict = {}
    else:
        by_sec = defaultdict(set)
        for t in tickers:
            sec = sector_of(t)
            if sec and sec != "기타":
                by_sec[_key(sec, _market_of(t))].add(t)
    all_data = load_all()
    day = all_data.setdefault(daykey, {})
    for rec in day.values():
        hits = rec.get("hits")
        if isinstance(hits, dict):
            hits.pop(tab, None)
    for key, tickers_set in by_sec.items():
        rec = day.setdefault(key, {})
        hits = rec.setdefault("hits", {})
        hits[tab] = sorted(tickers_set)
    _save(all_data)


def hit_tab_count(daykey: str, sector: str, market: str) -> int:
    """해당 (섹터,시장)이 오늘 몇 개 탭에서 히트가 나왔는지(탭 수, 종목 수 아님)."""
    rec = load_all().get(daykey, {}).get(_key(sector, market), {})
    hits = rec.get("hits", {})
    return sum(1 for tab, tks in hits.items() if tks)
