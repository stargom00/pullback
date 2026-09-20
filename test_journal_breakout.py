"""v5.274 — 일지 "돌파 기록" 3칸.

확정 정의(사용자, 2026-09-20): D+0 = **종가 > 피벗인 첫 날**(고가 터치는 아님),
D+1~3도 종가 기준, 등록일 이후만, 재돌파 시 리셋 + 이전 시도는 "실패 N회",
빈칸은 "—"(아직 안 지남)과 "?"(지났는데 봉 없음)를 **구분**한다.
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


def test_registration_cutoff_also_drops_earlier_failures():
    """등록 전의 실패한 돌파도 내 기록이 아니다."""
    closes = [101, 95, 102, 94] + [90] * 3 + [105] * 3
    dates, c, v = mk(closes)
    before = J.analyze_breakout_record(dates, c, v, PIVOT)
    after = J.analyze_breakout_record(dates, c, v, PIVOT, since=dates[5])
    assert before["failed"] == 2, before["failed"]
    assert after["failed"] == 0, "등록 전 실패가 딸려 들어왔다"


def test_registration_day_itself_is_excluded():
    closes = [101] * 5
    dates, c, v = mk(closes)
    r = J.analyze_breakout_record(dates, c, v, PIVOT, since=dates[0])
    assert r["d0_index"] == 1, r


# ── 재돌파 ──────────────────────────────────────────────────────────
def test_falling_back_below_resets_and_counts_a_failure():
    #      0-2 아래   3 돌파   4 이탈    5-8 재돌파
    r, _ = run([90, 90, 90, 101, 99, 103, 104, 105, 106])
    assert r["ok"] and r["d0_index"] == 5, r
    assert r["failed"] == 1, r["failed"]


def test_multiple_failures_are_counted():
    r, _ = run([90, 101, 95, 102, 94, 103, 96, 105, 106, 107])
    assert r["failed"] == 3, r["failed"]
    assert r["d0_index"] == 7, r["d0_index"]


def test_all_attempts_failed_reports_the_count():
    r, _ = run([90, 101, 95, 102, 94])
    assert not r["ok"]
    assert r["failed"] == 2 and "실패 2회" in r["reason"], r


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


def test_above_is_structurally_always_true_after_the_reset_rule():
    """**사양 충돌을 테스트로 고정한다.**

    "재돌파 시 리셋"과 "D+1·2·3 피벗 위 여부(✓/✗)"는 같이 성립할 수 없다:
    D+0 뒤에 종가가 피벗 아래로 가면 그 순간 D+0이 리셋되므로, 살아남은 D+0의
    뒤 봉들은 **정의상 전부 피벗 위**다. 즉 ✗는 절대 안 나온다.

    지금은 사양대로 두되(리셋 우선) 이 결과를 명시해 둔다 — 사용자가 ✗를
    보고 싶다면 "첫 D+0을 고정 표시"로 규칙을 바꿔야 한다.
    """
    import itertools
    for pattern in itertools.product([95.0, 105.0], repeat=6):
        closes = [90.0] * 5 + list(pattern)
        dates, c, v = mk(closes)
        r = J.analyze_breakout_record(dates, c, v, PIVOT, asof=dates[-1])
        for f in r["follow"]:
            if "above" in f:
                assert f["above"] is True, (pattern, r)


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
