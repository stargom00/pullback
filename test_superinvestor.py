"""tools/superinvestor(슈퍼인베스터 13F 스크리너, Phase 1) 테스트.

[구성]
① 순수 로직(네트워크 없음) — 13F 함정 1·2·3·4·5·6·7·8 + 분류·cluster·컨센서스
② 실데이터 대조(네트워크/캐시 필요, 없으면 skip)
   · 버크셔 최근 분기 상위 10: **벌크 데이터셋 vs EDGAR 원본 XML**(독립 경로)
   · value 단위 정규화: 2022-11 제출분(천 달러) vs 2023-02 제출분(달러) 실측
   · PUT/CALL 제외: Duquesne(옵션 보유 실제 9행)로 원본에 있고 결과엔 없음을 확인
   · 분할 보정: 대상 창에 실제 분할이 있던 종목으로 배수·재분류 확인
   · Dataroma 분류 일치율(검증 스크립트 실행)

[사보타주 확인 — 2026-09-28, 전부 FAIL 확인 후 원복]
① `keep_holding_row`에서 PUTCALL 검사 제거 → test_putcall_excluded_* 2건 FAIL
② `normalize_value`에서 천 달러 분기 제거 → test_value_unit_* 2건 FAIL
③ `classify_history`에서 split_factor 무시(항상 1.0) → test_split_* 2건 FAIL
④ `cluster_metrics`의 N/A 규칙(지배분기) 제거 → test_cluster_na_* 3건 FAIL
⑤ `apply_amendments`의 NEW HOLDINGS 분기 제거(전부 교체) → test_amend_new_holdings FAIL
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, timedelta

import pytest

_ROOT = os.path.dirname(os.path.abspath(__file__))
_TOOL = os.path.join(_ROOT, "tools", "superinvestor")
sys.path.insert(0, _TOOL)

import figi                      # noqa: E402
import screen                    # noqa: E402
import secdata                   # noqa: E402

Q = [date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30),
     date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)]
BRK_CIK = "0001067983"


# ══════════════════════════════════════════════════════════════════════
# ① 순수 로직
# ══════════════════════════════════════════════════════════════════════

# ── 함정 1·2 — PUT/CALL·PRN 제외 ──

def _row(**kw):
    base = {"PUTCALL": "", "SSHPRNAMTTYPE": "SH"}
    base.update(kw)
    return base


def test_putcall_excluded_unit():
    assert secdata.keep_holding_row(_row()) is True
    assert secdata.keep_holding_row(_row(PUTCALL="PUT")) is False
    assert secdata.keep_holding_row(_row(PUTCALL="Put")) is False
    assert secdata.keep_holding_row(_row(PUTCALL="CALL")) is False


def test_prn_excluded_unit():
    assert secdata.keep_holding_row(_row(SSHPRNAMTTYPE="PRN")) is False
    assert secdata.keep_holding_row(_row(SSHPRNAMTTYPE="sh")) is True
    assert secdata.keep_holding_row(_row(SSHPRNAMTTYPE="")) is False


# ── 함정 3 — VALUE 단위 ──

def test_value_unit_boundary():
    # SEC readme: "Starting on January 3, 2023, market value is reported rounded to the
    # nearest dollar. Previously, market value was reported in thousands."
    assert secdata.normalize_value(1000, date(2023, 1, 2)) == 1_000_000
    assert secdata.normalize_value(1000, date(2023, 1, 3)) == 1000
    assert secdata.normalize_value(1000, date(2022, 11, 14)) == 1_000_000
    assert secdata.normalize_value(1000, date(2026, 8, 14)) == 1000


def test_value_unit_constant_matches_readme_rule():
    assert secdata.VALUE_IN_DOLLARS_FROM == date(2023, 1, 3)
    assert secdata.THOUSANDS == 1000


# ── 함정 5 — 같은 CUSIP 합산 ──

def test_aggregate_by_cusip_sums_and_ors_late_flag():
    rows = [{"cusip": "02005N100", "name": "ALLY", "shares": 12561737, "value_usd": 577211815},
            {"cusip": "02005N100", "name": "ALLY", "shares": 2803875, "value_usd": 128838056,
             "late_disclosed": True},
            {"cusip": "037833100", "name": "APPLE", "shares": 100, "value_usd": 1000}]
    got = secdata.aggregate_by_cusip(rows)
    assert got["02005N100"]["shares"] == 12561737 + 2803875
    assert got["02005N100"]["value_usd"] == 577211815 + 128838056
    assert got["02005N100"]["late_disclosed"] is True
    assert got["037833100"]["late_disclosed"] is False


# ── 함정 4·7 — 정정 제출 ──

def _filing(acc, typ, day, amend=False, atype="", rows=None, no=0):
    return secdata.Filing(accession=acc, cik="1", period=Q[-1],
                          filing_date=date(2026, 8, day), submission_type=typ,
                          is_amendment=amend, amendment_no=no, amendment_type=atype,
                          conf_denied_expired=False, rows=rows or [])


def _r(cusip, shares, value=1000.0):
    return {"cusip": cusip, "name": cusip, "shares": shares, "value_usd": value,
            "late_disclosed": False}


def test_amend_restatement_replaces():
    base = _filing("a", "13F-HR", 14, rows=[_r("A", 100), _r("B", 200)])
    amd = _filing("b", "13F-HR/A", 20, amend=True, atype="RESTATEMENT",
                  rows=[_r("A", 50)], no=1)
    ph = secdata.apply_amendments([base, amd])
    assert set(ph.holdings) == {"A"}, "재작성은 기존 행을 전부 대체해야 한다"
    assert ph.holdings["A"]["shares"] == 50
    assert ph.restated is True and ph.late_disclosed_cusips == set()


def test_amend_new_holdings_appends_and_flags_late():
    base = _filing("a", "13F-HR", 14, rows=[_r("A", 100)])
    amd = _filing("b", "13F-HR/A", 20, amend=True, atype="NEW HOLDINGS",
                  rows=[_r("C", 300)], no=1)
    ph = secdata.apply_amendments([base, amd])
    assert set(ph.holdings) == {"A", "C"}, "추가분 정정은 기존 행을 지우면 안 된다"
    assert ph.holdings["C"]["late_disclosed"] is True   # 함정 7 — 뒤늦게 공개된 보유
    assert ph.holdings["A"]["late_disclosed"] is False
    assert ph.restated is False


def test_amend_unknown_type_treated_as_restatement():
    base = _filing("a", "13F-HR", 14, rows=[_r("A", 100)])
    amd = _filing("b", "13F-HR/A", 20, amend=True, atype="", rows=[_r("B", 5)], no=1)
    ph = secdata.apply_amendments([base, amd])
    assert set(ph.holdings) == {"B"}, "유형 불명 정정은 보수적으로 교체(주식 수 부풀림 방지)"


def test_notice_only_is_not_reported():
    nt = _filing("a", "13F-NT", 14)
    ph = secdata.apply_amendments([nt])
    assert ph.reported is False and ph.holdings == {}


# ── 분류 + 미보고 분기(가짜 NEW/EXIT 방지) ──

def test_classify_basic():
    assert screen.classify(0, 100) == "NEW"
    assert screen.classify(100, 0) == "EXIT"
    assert screen.classify(100, 150) == "ADD"
    assert screen.classify(150, 100) == "REDUCE"
    assert screen.classify(100, 100) == "HOLD"


def test_classify_eps_tolerance():
    eps = screen.CLASSIFY_EPS_PCT
    assert screen.classify(1_000_000, 1_000_000 * (1 + eps / 200)) == "HOLD"
    assert screen.classify(1_000_000, 1_000_000 * (1 + eps / 50)) == "ADD"


def test_unreported_quarter_makes_no_fake_new_or_exit():
    # 2025-12-31이 미보고(None)면 그 분기를 건너뛰고 앞뒤를 직접 비교해야 한다
    shares = {Q[0]: 100.0, Q[1]: 100.0, Q[2]: 100.0, Q[3]: None, Q[4]: 100.0, Q[5]: 100.0}
    rows = screen.classify_history(shares, Q)
    got = {r["period"]: r["klass"] for r in rows}
    assert Q[3] not in got, "미보고 분기는 분류 자체를 만들면 안 된다"
    assert got[Q[4]] == "HOLD", "미보고를 0으로 읽으면 여기가 NEW가 된다"
    assert "EXIT" not in got.values() and "NEW" not in got.values()


def test_first_quarter_has_no_classification():
    rows = screen.classify_history({q: 10.0 for q in Q}, Q)
    assert all(r["period"] != Q[0] for r in rows), "비교 기준이 없는 첫 분기는 분류 불가"


# ── 함정 6 — 분할 보정(순수 부분) ──

def test_split_factor_changes_classification_unit():
    # 분기말 이후 4:1 분할 → 보고 주식 수가 4배로 늘어 보인다. 보정 없으면 가짜 ADD.
    shares = {Q[4]: 1_000_000.0, Q[5]: 4_000_000.0}
    periods = [Q[4], Q[5]]
    naive = screen.classify_history(shares, periods)
    assert naive[0]["klass"] == "ADD", "보정 전에는 ADD로 보이는 것이 정상(픽스처 전제)"
    adjusted = screen.classify_history(shares, periods,
                                      split_factor=lambda p: 4.0 if p == Q[4] else 1.0)
    assert adjusted[0]["klass"] == "HOLD", "분할 보정하면 HOLD여야 한다"


# ── 추정 매입단가·cluster ──

def test_estimate_cost_uses_increases_only():
    est = screen.estimate_investor_cost([
        {"period": Q[4], "delta_shares": 100, "vwap_approx": 10.0, "low": 9, "high": 11},
        {"period": Q[5], "delta_shares": 300, "vwap_approx": 20.0, "low": 18, "high": 22},
        {"period": Q[3], "delta_shares": -500, "vwap_approx": 5.0, "low": 4, "high": 6},
    ])
    assert est["est_cost"] == pytest.approx((100 * 10 + 300 * 20) / 400)
    assert est["shares_added"] == 400
    assert est["band_low"] == 9 and est["band_high"] == 22
    assert est["dominant_quarter"] is None      # 25%/75% — 지배분기 없음


def test_cluster_na_same_single_quarter():
    """같은 분기에 3명이 NEW → 세 추정단가가 모두 그 분기 VWAP → 밀집도 N/A."""
    one = lambda: screen.estimate_investor_cost(      # noqa: E731
        [{"period": Q[5], "delta_shares": 100, "vwap_approx": 50.0, "low": 45, "high": 55}])
    cl = screen.cluster_metrics({"A": one(), "B": one(), "C": one()})
    assert cl["n_investors_with_cost"] == 3
    assert cl["n_distinct_quarters"] == 1 and cl["n_effective_quarters"] == 1
    assert cl["cluster_dispersion"] is None, "0이 아니라 N/A여야 한다(가짜 정밀도 금지)"
    assert "같은 분기" in cl["dispersion_na_reason"]
    assert cl["cluster_center"] == 50.0        # 중심값 자체는 계산된다


def test_cluster_na_dominant_quarter_leak():
    """실측 EQH 사례 — 한 투자자가 무의미하게 작은 분기를 하나 더 갖고 있어도
    지배분기가 같으면 N/A다(합집합으로 세면 이 케이스가 새어나갔다)."""
    a = screen.estimate_investor_cost([
        {"period": Q[4], "delta_shares": 28_245, "vwap_approx": 40.0, "low": 35, "high": 50},
        {"period": Q[5], "delta_shares": 9_489_037, "vwap_approx": 41.9, "low": 36, "high": 47}])
    b = screen.estimate_investor_cost([
        {"period": Q[5], "delta_shares": 5_398_896, "vwap_approx": 41.9, "low": 36, "high": 47}])
    cl = screen.cluster_metrics({"Harris": a, "Viking": b})
    assert cl["n_distinct_quarters"] == 2, "합집합은 2분기(픽스처 전제)"
    assert cl["n_effective_quarters"] == 1
    assert cl["cluster_dispersion"] is None


def test_cluster_dispersion_computed_when_quarters_differ():
    a = screen.estimate_investor_cost(
        [{"period": Q[4], "delta_shares": 100, "vwap_approx": 40.0, "low": 35, "high": 45}])
    b = screen.estimate_investor_cost(
        [{"period": Q[5], "delta_shares": 100, "vwap_approx": 60.0, "low": 55, "high": 65}])
    cl = screen.cluster_metrics({"A": a, "B": b})
    assert cl["n_effective_quarters"] == 2
    assert cl["cluster_dispersion"] == pytest.approx((60 - 40) / 50)
    assert cl["dispersion_na_reason"] is None


def test_cluster_na_single_investor():
    a = screen.estimate_investor_cost(
        [{"period": Q[5], "delta_shares": 100, "vwap_approx": 40.0, "low": 35, "high": 45}])
    cl = screen.cluster_metrics({"A": a})
    assert cl["cluster_dispersion"] is None and "1명" in cl["dispersion_na_reason"]


def test_band_position_and_gap():
    assert screen.band_position(10, 20, 30) == "below"
    assert screen.band_position(25, 20, 30) == "in"
    assert screen.band_position(35, 20, 30) == "above"
    assert screen.band_position(None, 20, 30) is None
    assert screen.gap_pct(110, 100) == 10.0
    assert screen.gap_pct(110, None) is None


def test_consensus_weights_not_just_count():
    classes = {"big": {Q[5]: "ADD"}, "small": {Q[5]: "NEW"}, "holder": {Q[5]: "HOLD"}}
    weights = {"big": {Q[5]: 20.0}, "small": {Q[5]: 0.5}, "holder": {Q[5]: 30.0}}
    got = screen.consensus_pct(classes, weights, Q[5])
    assert got["n_new_add_latest"] == 2
    assert got["consensus_latest_pct"] == pytest.approx(20.5), "HOLD는 컨센서스에서 빠진다"


def test_consensus_counts_each_investor_once_in_window():
    classes = {"a": {Q[3]: "ADD", Q[4]: "ADD", Q[5]: "HOLD"}}
    weights = {"a": {Q[3]: 1.0, Q[4]: 5.0, Q[5]: 5.0}}
    got = screen.consensus_pct(classes, weights, Q[5])
    assert got["n_new_add_5q"] == 1 and got["consensus_5q_pct"] == pytest.approx(5.0)
    assert got["n_new_add_latest"] == 0


# ── 함정 8 — 증권 유형 ──

@pytest.mark.parametrize("st,st2,sector,want", [
    ("ETP", "", "Equity", "etf"),
    ("Open-End Fund", "", "Equity", "etf"),
    ("ETN", "", "Equity", "etf"),
    ("ADR", "Depositary Receipt", "Equity", "adr"),
    ("Preference", "", "Equity", "preferred"),
    ("PFD ADR", "", "Equity", "preferred"),      # 우선주 우선(자본구조 구분)
    ("Common Stock", "Common Stock", "Equity", "common"),
    ("REIT", "", "Equity", "common"),
    ("US NON-CONVERTIBLE", "", "Corp", "non_equity"),
    ("Something New", "", "Equity", "other"),
])
def test_security_kind(st, st2, sector, want):
    assert figi.security_kind(st, st2, sector) == want


def test_pick_us_listing_prefers_equity_over_bond():
    data = [{"ticker": "GOOGL 6.25 05/15/29 A", "marketSector": "Corp", "exchCode": "US"},
            {"ticker": "GOOGL", "marketSector": "Equity", "exchCode": "US"}]
    assert figi.pick_us_listing(data)["ticker"] == "GOOGL"


def test_yahoo_symbol_class_separator():
    import prices as px
    assert px.yahoo_symbol("BRK/B") == "BRK-B"
    assert px.yahoo_symbol("AAPL") == "AAPL"


# ── 설정·시점 ──

def test_investors_config_has_list_fixed_date_and_unique_ciks():
    invs = secdata.load_investors()          # 누락/중복이면 RuntimeError
    assert len(invs) >= 20, "슈퍼인베스터 20~30명 규모여야 한다"
    assert all(i["list_fixed_date"] for i in invs)
    banned = ("VANGUARD", "BLACKROCK", "STATE STREET", "FMR ", "GEODE", "RENAISSANCE",
              "TWO SIGMA", "D. E. SHAW", "CITADEL", "MILLENNIUM", "AQR", "DIMENSIONAL",
              "NORTHERN TRUST", "BRIDGEWATER")
    for i in invs:
        assert not any(b in i["name"].upper() for b in banned), f"지수형·퀀트 제외 위반: {i['name']}"


def test_quarter_ends_respects_45day_filing_deadline():
    # 2026-06-30 분기는 2026-08-14까지 제출 → 8/13에는 아직 최신 분기가 아니다
    assert secdata.quarter_ends(1, date(2026, 8, 13))[-1] == date(2026, 3, 31)
    assert secdata.quarter_ends(1, date(2026, 8, 14))[-1] == date(2026, 6, 30)
    assert secdata.quarter_ends(3, date(2026, 9, 28)) == [
        date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)]


def test_all_outputs_carry_disclaimer():
    """모든 출력 머리에 "관심 신호 · 측정 전" — CSV 2개 + 콘솔 요약."""
    src = open(os.path.join(_TOOL, "run.py"), encoding="utf-8").read()
    assert src.count("CSV_HEADER_NOTE") >= 3, "CSV 머리말이 두 파일 모두에 들어가야 한다"
    assert screen.SIGNAL_DISCLAIMER in src
    assert src.count(f"{{screen.SIGNAL_DISCLAIMER}}") >= 2, "콘솔 요약에도 명시"


# ══════════════════════════════════════════════════════════════════════
# ② 실데이터 대조
# ══════════════════════════════════════════════════════════════════════

def _zip_for(label: str) -> str | None:
    p = os.path.join(_TOOL, "cache", "sec", f"{label}_form13f.zip")
    return p if os.path.exists(p) else None


LATEST_LABEL = "01jun2026-31aug2026"
needs_bulk = pytest.mark.skipif(_zip_for(LATEST_LABEL) is None,
                                reason="SEC 벌크 zip 캐시 없음(run.py 먼저 실행)")


@pytest.fixture(scope="module")
def brk_latest():
    """버크셔 2026-06-30 — 벌크 데이터셋 경로."""
    filings = secdata.load_window(_zip_for(LATEST_LABEL), {BRK_CIK}, {date(2026, 6, 30)})
    ph = secdata.apply_amendments(filings)
    assert ph is not None and ph.reported
    return ph


@pytest.fixture(scope="module")
def brk_edgar():
    """버크셔 2026-06-30 — EDGAR 원본 XML 경로(독립 소스)."""
    import edgar
    fl = [f for f in edgar.list_13f_filings(BRK_CIK)
          if f["period"] == date(2026, 6, 30) and f["form"] == "13F-HR"]
    assert fl, "EDGAR에서 2026Q2 13F-HR을 못 찾음"
    return edgar.infotable_rows(BRK_CIK, fl[0]["accession"])


@needs_bulk
def test_berkshire_top10_matches_edgar_xml(brk_latest, brk_edgar):
    """버크셔 상위 10 종목 — 벌크 파이프라인 vs EDGAR 공개 원본.
    CUSIP·주식수·가치가 모두 일치해야 한다(함정 5 합산 포함)."""
    agg = {}
    for r in brk_edgar:
        if r["putcall"] or r["type"] != "SH":        # 함정 1·2를 같은 규칙으로
            continue
        a = agg.setdefault(r["cusip"], {"shares": 0.0, "value": 0.0, "name": r["name"]})
        a["shares"] += r["shares"]
        a["value"] += r["value_raw"]                # 2023-01-03 이후 제출 → 달러
    top_edgar = sorted(agg.items(), key=lambda kv: -kv[1]["value"])[:10]
    top_ours = sorted(brk_latest.holdings.items(), key=lambda kv: -kv[1]["value_usd"])[:10]
    assert [c for c, _ in top_ours] == [c for c, _ in top_edgar], "상위 10 종목·순서 불일치"
    for (c, ours), (_, theirs) in zip(top_ours, top_edgar):
        assert ours["shares"] == pytest.approx(theirs["shares"]), c
        assert ours["value_usd"] == pytest.approx(theirs["value"]), c
    # 상위 10은 전체의 상당 부분이어야 한다(집중 포트폴리오 — 파서가 행을 흘렸는지 감지)
    assert sum(v["value"] for _, v in top_edgar) / sum(v["value"] for v in agg.values()) > 0.5


def test_value_unit_normalization_on_real_filings():
    """실데이터로 단위 규칙 확인 — 2022-11-14 제출(천 달러) vs 2023-02-14 제출(달러).
    같은 버크셔 포트폴리오라 정규화 후 두 총액이 같은 자릿수($1000억대)여야 한다."""
    import edgar
    fl = edgar.list_13f_filings(BRK_CIK, include_older=True)
    old = next((f for f in fl if f["filing_date"] == date(2022, 11, 14)), None)
    new = next((f for f in fl if f["filing_date"].year == 2023 and f["filing_date"].month == 2), None)
    if not old or not new:
        pytest.skip("2022-11/2023-02 제출분을 EDGAR에서 못 찾음")
    totals = {}
    for tag, f in (("old", old), ("new", new)):
        rows = [r for r in edgar.infotable_rows(BRK_CIK, f["accession"])
                if not r["putcall"] and r["type"] == "SH"]
        raw = sum(r["value_raw"] for r in rows)
        totals[tag] = (raw, sum(secdata.normalize_value(r["value_raw"], f["filing_date"])
                                for r in rows))
    # 원본 단위는 실제로 1000배 차이가 난다(전제 확인 — 아니면 이 테스트가 무의미)
    assert totals["old"][0] * 500 < totals["new"][0], \
        f"원본 단위 차이 전제 실패: {totals['old'][0]:.0f} vs {totals['new'][0]:.0f}"
    for tag in ("old", "new"):
        assert 1e11 < totals[tag][1] < 1e12, \
            f"{tag} 정규화 총액이 $1000억대가 아니다: {totals[tag][1]:.0f}"
    ratio = totals["new"][1] / totals["old"][1]
    assert 0.5 < ratio < 2.0, f"정규화 후 두 분기 총액 비율이 비정상: {ratio:.2f}"


@needs_bulk
def test_putcall_excluded_on_real_investor():
    """Duquesne(옵션 실제 보유)로 함정 1 확인 — 원본엔 PUT/CALL 행이 있고 결과엔 없다."""
    import csv as _csv
    import io
    import zipfile
    inv = next(i for i in secdata.load_investors() if "Duquesne" in i["short"])
    zp = _zip_for(LATEST_LABEL)
    with zipfile.ZipFile(zp) as zf:
        accs = {r["ACCESSION_NUMBER"] for r in secdata._read_tsv(zf, "SUBMISSION.tsv")
                if (r["CIK"] or "").lstrip("0") == inv["cik_plain"]
                and r["PERIODOFREPORT"] == "30-JUN-2026"}
        assert accs
        raw_rows = [r for r in secdata._read_tsv(zf, "INFOTABLE.tsv")
                    if r["ACCESSION_NUMBER"] in accs]
    put_rows = [r for r in raw_rows if (r["PUTCALL"] or "").strip()]
    assert len(put_rows) >= 3, f"픽스처 전제 실패: PUT/CALL 행 {len(put_rows)}건"
    ph = secdata.apply_amendments(
        secdata.load_window(zp, {inv["cik_plain"]}, {date(2026, 6, 30)}))
    put_cusips = {r["CUSIP"].upper() for r in put_rows}
    put_only = {c for c in put_cusips
                if not any((r["CUSIP"] or "").upper() == c and not (r["PUTCALL"] or "").strip()
                           for r in raw_rows)}
    assert put_only, "픽스처 전제 실패: 옵션으로만 보유한 CUSIP이 없다"
    assert put_only & set(ph.holdings) == set(), \
        f"옵션 전용 보유가 결과에 남았다: {sorted(put_only & set(ph.holdings))}"


@pytest.mark.skipif(not os.path.exists(os.path.join(_TOOL, "cache", "prices")),
                    reason="가격 캐시 없음(run.py 먼저 실행)")
def test_split_adjustment_on_real_split_ticker():
    """대상 창(2025Q1~2026Q2)에 실제 분할이 있던 종목으로 함정 6 확인."""
    import glob
    import pandas as pd
    import prices as px
    files = sorted(glob.glob(os.path.join(_TOOL, "cache", "prices", "daily_*.pkl")))
    cache = pd.read_pickle(files[-1])
    found = None
    for t, df in cache.items():
        sp = px.splits_in_window(df, date(2025, 4, 1), date(2026, 6, 30))
        if sp:
            found = (t, sp)
            break
    if not found:
        pytest.skip("캐시된 종목 중 대상 창에 분할이 있는 종목이 없다")
    ticker, sp = found
    df = cache[ticker]
    split_day, ratio = date.fromisoformat(sp[0][0]), sp[0][1]
    # 분할일이 속한 분기의 **직전** 분기말(그 시점 13F 주식 수는 분할 전 기준)
    before_q = date(split_day.year, ((split_day.month - 1) // 3) * 3 + 1, 1) - timedelta(days=1)
    f_before = px.split_factor_after(df, before_q)
    f_after = px.split_factor_after(df, date(2026, 6, 30))
    assert f_before >= ratio, f"{ticker}: 분할 이전 분기말 배수 {f_before} < {ratio}"
    assert f_after == 1.0 or f_after < f_before
    # 보정 없이 비교하면 가짜 ADD, 보정하면 HOLD
    periods = [before_q, date(2026, 6, 30)]
    shares = {periods[0]: 1_000_000.0, periods[1]: 1_000_000.0 * f_before}
    naive = screen.classify_history(shares, periods)
    adj = screen.classify_history(shares, periods,
                                 split_factor=lambda p: px.split_factor_after(df, p))
    assert naive[0]["klass"] == "ADD"
    assert adj[0]["klass"] == "HOLD", f"{ticker} 분할 보정 후에도 ADD로 분류됨"


def test_dataroma_classification_agreement():
    """Dataroma 공개 NEW/ADD와 투자자 3명 대조 — 분류 일치율을 **보고**한다.
    (스모크 하한 0.5만 둔다 — 목표치가 아니라 "완전히 깨졌으면 잡는" 선.
    관례 차이는 validate_dataroma.py docstring 참고.)"""
    if _zip_for(LATEST_LABEL) is None:
        pytest.skip("SEC 벌크 zip 캐시 없음")
    import validate_dataroma as vd
    try:
        res = vd.run(["BRK", "psc", "tp"], quarters=5, windows=6)
    except Exception as e:                      # 네트워크 차단·페이지 개편 등
        pytest.skip(f"Dataroma 대조 불가: {e}")
    assert len(res) == 3
    for code, r in res.items():
        assert r["n_common"] >= 5, f"{code}: 공통 종목 {r['n_common']}개 — 대조 불가"
        assert r["match_rate"] is not None
        print(f"[dataroma] {code} 일치율 {r['match_rate'] * 100:.1f}% (공통 {r['n_common']})")
        assert r["match_rate"] >= 0.5, f"{code} 일치율 {r['match_rate']:.2f} — 분류 로직 점검 필요"


@needs_bulk
def test_run_smoke_no_prices():
    """실행기 자체가 13F 단계까지 돌고 CSV 머리말을 쓰는지(네트워크 최소)."""
    out = subprocess.run([sys.executable, "run.py", "--no-prices"], cwd=_TOOL,
                         capture_output=True, text=True, timeout=1800)
    assert out.returncode == 0, out.stderr[-2000:]
    assert screen.SIGNAL_DISCLAIMER in out.stdout
    unmapped = os.path.join(_TOOL, "output", "unmapped_cusips.csv")
    assert os.path.exists(unmapped)
    head = open(unmapped, encoding="utf-8-sig").readline()
    assert head.startswith("#") and screen.SIGNAL_DISCLAIMER in head
