"""v5.308 — ① 저점 재시도 KR 장외 제한 ② 1회성 메모리 진단 제거 ③ 월봉 창 축소 가드.

배경(2026-10-01 운영 조사): 홈 "불러오기 실패" + RSS 1,009→1,273MB 지속 증가.
확정된 결함 두 개를 고친다.
  ① 재시도(60분×3)가 월봉 08:00 실패 시 **09:00·10:00 = KR 장중**으로 들어왔다 —
     v5.307이 슬롯을 09:20→08:00으로 옮긴 이유(장 시작 스캔과 메모리 경합)를 되돌리는 구조.
  ② `MEMORY_DIAG`/tracemalloc/`/api/debug/memory`가 "1회성"이라는 주석과 달리 운영에
     상시 켜져 있었다(호출당 37~50초). 진단이 끝났으므로 제거.
월봉 fetch 창 축소(10년→5년)는 **시도했다가 되돌렸다** — 같은 기준봉(2026-09-30) 비교에서
hit 8→7건, 공통 2건뿐이었다(원인·수치는 lowpoint.py의 KR_DAYS 주석). 지시대로 "다르면
중단"이라 창은 10년을 유지하고, 요구 봉수 가드만 이 파일에 남긴다.

사보타주 확인(2026-10-01, 전부 FAIL 확인 후 원복):
① `_lowpoint_due`에서 `_lowpoint_retry_blocked` 호출 제거 → 재시도 테스트 3건 FAIL
② `_lowpoint_retry_blocked`가 항상 False → 같은 3건 FAIL
③ `US_PERIOD["month"]="2y"`(번인 미달) → test_month_window_covers_burn_in FAIL
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

import app

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_ROOT, "scripts", "screens"))
import lowpoint as lp  # noqa: E402

KST = timezone(timedelta(hours=9))


def _k(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=KST)


# ── ① 재시도 창(순수 판정) ───────────────────────────────────────────

@pytest.mark.parametrize("now,blocked", [
    ("2026-10-01 08:59", False),   # 장전
    ("2026-10-01 09:00", True),    # 개장 정각
    ("2026-10-01 10:00", True),    # 장중(실제로 재시도가 들어왔던 시각)
    ("2026-10-01 15:29", True),
    ("2026-10-01 15:39", True),    # 마감 직후 여유 10분 안
    ("2026-10-01 15:40", False),   # 여기서부터 허용
    ("2026-10-01 20:00", False),
    ("2026-10-03 10:00", False),   # 토요일 — 창 자체가 없다
    ("2026-10-04 10:00", False),   # 일요일
])
def test_retry_blocked_window(now, blocked):
    assert app._lowpoint_retry_blocked(_k(now)) is blocked


def test_retry_window_follows_kr_holidays():
    """공휴일엔 장이 없으니 재시도를 막지 않는다 — is_trading_day 재사용 확인."""
    holiday = next(iter(sorted(app.KRX_HOLIDAYS_2026)))
    h = _k(f"{holiday} 10:00")
    assert app.is_trading_day("kr", h) is False
    assert app._lowpoint_retry_blocked(h) is False


def test_retry_window_is_kst_not_local():
    # NZDT(KST+4) 13:00 = KST 09:00 → 장중으로 막혀야 한다
    nz = datetime.fromisoformat("2026-10-01 13:00").replace(tzinfo=timezone(timedelta(hours=13)))
    assert app._lowpoint_retry_blocked(nz) is True


# ── ① 재시도 창(스케줄 판정과 결합) ─────────────────────────────────

def _failed_state(tf="month", target="2026-09-30", started="2026-10-01T08:00:00+09:00", attempts=1):
    return {tf: {"target": target, "status": "failed", "attempts": attempts,
                 "started_at": started, "finished_at": started}}


@pytest.mark.parametrize("now,expect", [
    ("2026-10-01 08:30", None),          # 재시도 간격(60분) 미경과
    ("2026-10-01 09:00", None),          # 간격은 됐지만 장중 → 미룸
    ("2026-10-01 10:00", None),          # 장중
    ("2026-10-01 15:39", None),          # 장중(여유 포함)
    ("2026-10-01 15:40", "2026-09-30"),  # 장외 — 재시도
    ("2026-10-01 22:00", "2026-09-30"),
])
def test_month_retry_defers_to_after_close(now, expect):
    assert app._lowpoint_due("month", _k(now), _failed_state()) == expect


def test_first_attempt_is_not_gated():
    """예약 시각(월봉 1일 08:00)의 **첫 시도**는 창 판정을 타지 않는다 —
    상태 파일에 그 라벨 기록이 없으면 바로 실행."""
    assert app._lowpoint_due("month", _k("2026-10-01 08:00"), {}) == "2026-09-30"
    # 설령 장중에 첫 시도 시각이 와도(슬롯이 장중으로 바뀐 경우) 첫 시도는 막지 않는다
    assert app._lowpoint_due("month", _k("2026-10-01 10:00"), {}) == "2026-09-30"


def test_week_saturday_retry_still_allowed():
    """주봉 토요일 09:00 실패 → 같은 날 10:00 재시도가 가능해야 한다(주말은 장외)."""
    st = _failed_state("week", "2026-10-02", "2026-10-03T09:00:00+09:00")
    assert app._lowpoint_due("week", _k("2026-10-03 10:00"), st) == "2026-10-02"


def test_max_attempts_still_caps():
    st = _failed_state(attempts=app.LOWPOINT_MAX_ATTEMPTS)
    assert app._lowpoint_due("month", _k("2026-10-01 20:00"), st) is None


def test_retry_block_window_constant():
    lo, hi = app.LOWPOINT_RETRY_BLOCK_HM
    assert (lo, hi) == (9 * 60, 15 * 60 + 40), "정규장 09:00~15:30 + 여유 10분"


# ── ② 메모리 진단 제거 ──────────────────────────────────────────────

def _app_code_without_module_docstring() -> str:
    """app.py 실행 코드만 — 모듈 docstring(= [변경 이력] changelog)은 과거 기록이라 제외."""
    src = open(os.path.join(_ROOT, "app.py"), encoding="utf-8").read()
    end = src.index('"""', src.index('"""') + 3) + 3
    return src[end:]


def _identifier_refs() -> dict:
    """**실행 코드**의 식별자 참조만 AST로 센다 — 주석·docstring의 서술(예: "RSS는 오르는데
    tracemalloc은 평평했다"는 과거 관측 기록)은 참조가 아니다. 문자열 검색으로 세면 그런
    서술까지 걸려 오탐한다(test_trace_const_audit.py가 AST를 쓰는 것과 같은 이유)."""
    import ast
    tree = ast.parse(open(os.path.join(_ROOT, "app.py"), encoding="utf-8").read())
    hits = {"MEMORY_DIAG": 0, "_deep_size": 0, "tracemalloc": 0}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in hits:
            hits[node.id] += 1
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "tracemalloc":
            hits["tracemalloc"] += 1
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] == "tracemalloc":
                    hits["tracemalloc"] += 1
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("tracemalloc"):
            hits["tracemalloc"] += 1
    return hits


def test_debug_memory_route_is_gone():
    paths = {getattr(r, "path", None) for r in app.app.routes}
    assert "/api/debug/memory" not in paths
    assert "/api/debugraw/{ticker}" in paths, "같이 지우면 안 되는 라우트가 사라졌다"


def test_memory_diag_references_are_zero_in_code():
    assert _identifier_refs() == {"MEMORY_DIAG": 0, "_deep_size": 0, "tracemalloc": 0}
    assert not hasattr(app, "MEMORY_DIAG")
    assert not hasattr(app, "_deep_size")
    # 함수 정의 자체도 없어야 한다(식별자 참조 0이어도 정의만 남을 수 있다)
    code = _app_code_without_module_docstring()
    assert "def _deep_size(" not in code
    assert "async def debug_memory(" not in code


def test_tracemalloc_not_running():
    import tracemalloc
    assert not tracemalloc.is_tracing(), "app import 과정에서 tracemalloc이 켜졌다"


def test_bot_read_paths_no_longer_expose_memory():
    assert "/api/debug/memory" not in app._BOT_READ_EXACT_PATHS


# ── ④ 실행 로그의 피크 RSS 기록은 유지 ──────────────────────────────

def test_lowpoint_run_logs_peak_rss():
    """진단을 지워도 저점 자동 실행 로그의 rss 기록은 남아야 한다(사용자 지시)."""
    assert callable(app._rss_mb)
    code = _app_code_without_module_docstring()
    body = code.split("async def _maybe_run_lowpoint(")[1].split("\n# ──")[0]
    assert body.count("_rss_mb()") >= 3, "시작·완료·실패 로그에서 rss를 찍어야 한다"
    assert "rss {rss0} → {_rss_mb()}MB" in body


# ── ③ 월봉 창 축소 가드 ─────────────────────────────────────────────

def test_month_window_is_still_ten_years():
    """축소 보류 상태를 고정한다 — 2026-10-01 실측에서 5년으로 줄이면 hit 목록이
    바뀌었다(8→7건, 공통 2건). 줄이려면 같은 기준봉 재현 비교를 먼저 통과해야 한다."""
    assert lp.KR_DAYS["month"] == 3700 and lp.US_PERIOD["month"] == "10y"


def test_month_window_covers_burn_in():
    """창이 최소 봉수 + RSI14보다 길어야 한다(v5.329: 최소 봉수 = RSI 요구치 17 → 31개월 — 창 10년이면 넉넉하다.
    창 길이 자체를 줄이면 안 되는 이유는 위 테스트 — 결과가 바뀐 2026-10-01 실측)."""
    need_months = lp.MIN_BARS["month"] + lp.RSI_PERIOD
    assert need_months == lp.MIN_BARS_RSI + lp.RSI_PERIOD
    us_years = int(re.fullmatch(r"(\d+)y", lp.US_PERIOD["month"]).group(1))
    assert us_years * 12 >= need_months, f"US {us_years}년 < 요구 {need_months}개월"
    kr_months = lp.KR_DAYS["month"] / 30.44
    assert kr_months >= need_months, f"KR {kr_months:.0f}개월 < 요구 {need_months}개월"


def test_week_window_unchanged():
    """주봉 창은 이번 변경 대상이 아니다(회귀 방지)."""
    assert lp.KR_DAYS["week"] == 1900 and lp.US_PERIOD["week"] == "5y"
    need_weeks = lp.MIN_BARS["week"] + lp.RSI_PERIOD
    assert lp.KR_DAYS["week"] / 7 >= need_weeks
