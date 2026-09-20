"""v5.274 — 일지 "돌파 기록" 3칸.

확정 정의(사용자, 2026-09-20): D+0 = **종가 > 피벗인 첫 날**(고가 터치는 아님),
D+1~3도 종가 기준, 등록일 이후만, **D+0은 한 번 잡히면 안 바뀐다**,
빈칸은 "—"(아직 안 지남)과 "?"(지났는데 봉 없음)를 **구분**한다.

재설계 경위: 처음엔 "종가가 피벗 아래로 가면 리셋, 다음 돌파가 새 D+0"이었다.
그런데 그러면 살아남은 D+0 뒤의 봉은 **정의상 전부 피벗 위**라 ✗가 구조적으로
나올 수 없었다(64패턴 전수로 확인해 테스트에 고정했었다). "돌파 실패를 기록"이
목적이므로 리셋을 버리고 첫 D+0을 고정했다.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import journal_breakout as J  # noqa: E402

PIVOT = 100.0


def mk(closes, vols=None, start=date(2026, 1, 5)):
    """평일만 쓰는 단순 봉열(주말 건너뜀 — 달력일 환산 테스트를 위해)."""
    dates, d = [], start
    while len(dates) < len(closes):
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return dates, [float(c) for c in closes], [float(v) for v in (vols or [1000.0] * len(closes))]


def run(closes, vols=None, since=None, asof=None):
    dates, c, v = mk(closes, vols)
    return J.analyze_breakout_record(dates, c, v, PIVOT, since=since, asof=asof), dates


# ── D+0 정의 ────────────────────────────────────────────────────────
def test_d0_is_the_first_close_above_pivot():
    r, dates = run([90] * 5 + [101] + [102] * 5)
    assert r["ok"] and r["d0_index"] == 5, r
    assert r["d0_date"] == dates[5]


def test_touching_the_pivot_intraday_is_not_a_breakout():
    """**종가 기준.** 고가가 뚫어도 종가가 피벗 이하면 돌파가 아니다."""
    r, _ = run([90] * 5 + [100] + [99] * 5)      # 종가가 정확히 피벗 = 돌파 아님
    assert not r["ok"], r
    assert "아직 돌파 없음" in r["reason"]


def test_registration_date_cuts_off_earlier_breakouts():
    """등록 전 돌파는 내 매매와 무관하다 — 세면 안 된다."""
    closes = [101] * 3 + [105] * 3          # 내내 피벗 위(리셋 없음)
    dates, c, v = mk(closes)
    r2 = J.analyze_breakout_record(dates, c, v, PIVOT, since=None)
    assert r2["d0_index"] == 0, "등록일을 안 주면 전체 기간을 본다"
    r = J.analyze_breakout_record(dates, c, v, PIVOT, since=dates[2])
    assert r["d0_index"] == 3, r            # 등록일(2) **다음** 봉부터


def test_registration_cutoff_moves_d0_past_earlier_breakouts():
    """등록 전 돌파는 D+0이 될 수 없다."""
    closes = [101, 95, 102, 94] + [90] * 3 + [105] * 3
    dates, c, v = mk(closes)
    before = J.analyze_breakout_record(dates, c, v, PIVOT)
    after = J.analyze_breakout_record(dates, c, v, PIVOT, since=dates[5])
    assert before["d0_index"] == 0, before["d0_index"]
    assert after["d0_index"] == 7, after["d0_index"]


def test_registration_day_itself_is_excluded():
    closes = [101] * 5
    dates, c, v = mk(closes)
    r = J.analyze_breakout_record(dates, c, v, PIVOT, since=dates[0])
    assert r["d0_index"] == 1, r


# ── D+0 고정 / 돌파 실패 ────────────────────────────────────────────
def test_d0_stays_put_when_price_falls_back_below():
    """**핵심 규칙.** D+0 뒤에 피벗을 잃어도 D+0은 안 옮겨진다 — 그래야 실패가
    기록으로 남는다(옮기면 성공한 돌파만 보이게 된다)."""
    r, _ = run([90, 90, 101, 99, 98, 105, 106, 107])
    assert r["d0_index"] == 2, r["d0_index"]
    got = [(f["day"], f["above"]) for f in r["follow"]]
    assert got == [(1, False), (2, False), (3, True)], got


def test_failed_counts_the_days_that_lost_the_pivot():
    """failed = D+1~3 중 종가가 피벗 아래였던 날 수(= 화면의 ✗ 개수)."""
    r, _ = run([90, 101, 99, 98, 97])
    assert r["d0_index"] == 1
    assert r["failed"] == 3, r["failed"]
    assert all(f["above"] is False for f in r["follow"])


def test_clean_breakout_has_no_failures():
    r, _ = run([90, 101, 102, 103, 104])
    assert r["failed"] == 0
    assert all(f["above"] is True for f in r["follow"])


def test_a_later_breakout_does_not_replace_d0():
    """D+3 뒤에 더 큰 돌파가 나와도 D+0은 그대로다."""
    r, _ = run([90, 101, 95, 94, 93, 120, 130])
    assert r["d0_index"] == 1, r["d0_index"]
    assert r["failed"] == 3


# ── 거래량 배수 ─────────────────────────────────────────────────────
def test_vol_mult_uses_bars_before_not_including_itself():
    """자기 자신을 평균에 넣으면 큰 거래량이 분모도 키워 배수가 눌린다."""
    closes = [90] * 10 + [101, 102, 103, 104]
    vols = [1000.0] * 10 + [5000.0, 2000.0, 1000.0, 1000.0]
    r, _ = run(closes, vols)
    assert r["d0_vol_mult"] == 5.0, r["d0_vol_mult"]


def test_follow_days_report_volume_and_above_flag():
    closes = [90] * 10 + [101, 102, 103, 104]
    vols = [1000.0] * 10 + [3000.0, 2000.0, 1500.0, 4000.0]
    r, _ = run(closes, vols)
    f = {x["day"]: x for x in r["follow"]}
    for k in (1, 2, 3):
        assert f[k]["above"] is True, f[k]
        assert f[k]["vol_mult"] is not None, f[k]


def test_the_cross_mark_is_actually_reachable():
    """리셋을 쓰던 시절 ✗는 **구조적으로 불가능**했다(64패턴 전수로 확인).
    첫 D+0 고정으로 바꾼 뒤에는 실제로 나와야 한다 — 안 나오면 열이 무의미하다."""
    import itertools
    seen = {True: 0, False: 0}
    for pattern in itertools.product([95.0, 105.0], repeat=6):
        closes = [90.0] * 5 + list(pattern)
        dates, c, v = mk(closes)
        r = J.analyze_breakout_record(dates, c, v, PIVOT, asof=dates[-1])
        for f in r["follow"]:
            if "above" in f:
                seen[f["above"]] += 1
    assert seen[False] > 0, "✗가 한 번도 안 나온다 — 리셋 규칙이 살아 있다"
    assert seen[True] > 0


# ── 빈칸 두 종류 ────────────────────────────────────────────────────
def test_blank_is_dash_when_the_day_has_not_arrived():
    """돌파 직후 조회 — D+1~3은 아직 안 온 것이지 없는 게 아니다."""
    dates, c, v = mk([90] * 5 + [101])
    r = J.analyze_breakout_record(dates, c, v, PIVOT, asof=dates[-1])
    assert [x.get("blank") for x in r["follow"]] == [J.NOT_YET] * 3, r["follow"]


def test_blank_is_question_mark_when_bars_should_exist():
    """D+0으로부터 한 달이 지났는데 뒤 봉이 없다 — 데이터가 빈 것이다."""
    dates, c, v = mk([90] * 5 + [101])
    r = J.analyze_breakout_record(dates, c, v, PIVOT,
                                  asof=dates[-1] + timedelta(days=30))
    assert [x.get("blank") for x in r["follow"]] == [J.MISSING] * 3, r["follow"]


def test_the_two_blanks_are_different_symbols():
    """같은 기호를 쓰면 '데이터 없음'과 '아직'이 화면에서 구분되지 않는다."""
    assert J.NOT_YET != J.MISSING


def test_partial_follow_mixes_values_and_blanks():
    dates, c, v = mk([90] * 5 + [101, 102])
    r = J.analyze_breakout_record(dates, c, v, PIVOT, asof=dates[-1])
    f = r["follow"]
    assert "vol_mult" in f[0] and f[0]["above"] is True
    assert f[1].get("blank") == J.NOT_YET and f[2].get("blank") == J.NOT_YET


# ── 방어 ────────────────────────────────────────────────────────────
def test_no_pivot_or_no_bars_is_reported_not_guessed():
    for args in (([], [], []), (None, None, None)):
        pass
    r = J.analyze_breakout_record([], [], [], PIVOT)
    assert not r["ok"] and r["reason"] == "봉 없음"
    dates, c, v = mk([100, 101])
    r2 = J.analyze_breakout_record(dates, c, v, None)
    assert not r2["ok"] and r2["reason"] == "봉 없음"


def test_keys_always_present_so_callers_do_not_keyerror():
    r = J.analyze_breakout_record([], [], [], PIVOT)
    for k in ("ok", "reason", "d0_index", "d0_date", "failed",
              "d0_vol_mult", "follow"):
        assert k in r, k
