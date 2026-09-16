"""v5.265 — 메모리 진단 엔드포인트(1회성).

배경: Railway Metrics에서 컨테이너 시작 직후 900MB / 1GB 축 90%, 09-17 자동
재배포(git push 아님 → OOM 유력). 로컬 실측으로는 KR+US 3,605종목 DataFrame이
**약 47MB**(종목당 13.3KB, 이미 float32)라 900MB가 설명되지 않는다 — 나머지가
무엇인지 **그 프로세스 안에서** 재기 위한 진단이다.

진단이 끝나면 엔드포인트·MEMORY_DIAG 환경변수·이 테스트를 함께 제거한다.
"""
import asyncio
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402


def _df(n=100):
    return pd.DataFrame(
        {c: [1.0] * n for c in ("Open", "High", "Low", "Close", "Volume")},
        index=pd.bdate_range("2025-01-01", periods=n))


def test_route_registered_and_bot_readable():
    paths = [r.path for r in app.app.routes if hasattr(r, "path")]
    assert "/api/debug/memory" in paths
    # 세션 쿠키 없이 API_READ_TOKEN으로 찍을 수 있어야 한다(설계된 접근 경로)
    assert app._is_bot_read_path("GET", "/api/debug/memory") is True


def test_disabled_by_default_and_says_how_to_enable():
    r = asyncio.run(app.debug_memory())
    assert r["enabled"] is False
    assert "tracemalloc" not in r
    assert "MEMORY_DIAG=1" in r["hint"]


def test_reports_rss_and_gc():
    r = asyncio.run(app.debug_memory())
    assert isinstance(r["rss_mb"], (int, float)) and r["rss_mb"] > 0
    assert len(r["gc_count"]) == 3 and len(r["gc_threshold"]) == 3


def test_dataframe_sized_precisely_not_by_getsizeof():
    """getsizeof(df)는 내부 블록을 안 세서 실제보다 훨씬 작게 나온다."""
    d = _df(500)
    got = app._deep_size(d)
    true = int(d.memory_usage(deep=True).sum())
    assert got == true, (got, true)
    # getsizeof(df)를 **더하면 안 된다** — 현재 pandas는 내부 블록을 이미 포함해
    # 이중 계산이 된다(작성 중 실제로 2배로 보고됐다).
    assert got < true + sys.getsizeof(d), "이중 계산 의심"


def test_cache_accounting_matches_contents():
    """캐시 보고값이 실제 보유 DataFrame 합계와 맞는가."""
    dfs = {f"T{i}.KS": _df(200) for i in range(20)}
    true = sum(int(x.memory_usage(deep=True).sum()) for x in dfs.values())
    got = app._deep_size({"data": dfs})
    assert got >= true, (got, true)
    assert got < true * 1.5, "과대집계 — 이중 계산 의심"


def test_size_failure_is_not_silently_zero():
    """측정 실패를 0으로 뭉개면 '캐시가 안 크다'는 잘못된 결론이 나온다.
    (작성 중 실제로 겪음 — pandas 미import로 전 항목이 0.0MB로 보고됐다.)"""
    class Hostile:
        def __sizeof__(self):
            raise RuntimeError("측정 불가")

    assert app._deep_size(Hostile()) < 0, "실패가 0으로 보고된다"
    r = app._deep_size({"a": Hostile()})
    assert r < 0, "하위 노드 실패가 상위 합계에서 사라졌다"


def test_budget_stops_runaway_walk():
    """수만 노드짜리 캐시에서 진단 자체가 요청을 잡아먹으면 안 된다."""
    deep = {"k": [{"x": i} for i in range(5000)]}
    budget = [50]
    app._deep_size(deep, _budget=budget)
    assert budget[0] <= 0, "예산이 소진되지 않았다 — 상한이 안 걸린다"


def test_tracemalloc_section_when_enabled(monkeypatch):
    import tracemalloc
    monkeypatch.setattr(app, "MEMORY_DIAG", True)
    started = tracemalloc.is_tracing()
    if not started:
        tracemalloc.start(3)
    try:
        junk = [_df(50) for _ in range(5)]          # noqa: F841 — 할당 만들기
        r = asyncio.run(app.debug_memory(top=5))
        assert r["enabled"] is True
        tm = r["tracemalloc"]
        assert "error" not in tm, tm
        assert tm["current_mb"] >= 0 and tm["peak_mb"] >= tm["current_mb"]
        assert 1 <= len(tm["top"]) <= 5
        assert ":" in tm["top"][0]["where"], tm["top"][0]
    finally:
        if not started:
            tracemalloc.stop()


def test_startup_only_traces_when_env_set():
    src = Path(app.__file__).read_text(encoding="utf-8")
    i = src.index("async def _start_scheduler")
    body = src[i:i + 600]
    assert "if MEMORY_DIAG:" in body, "무조건 tracemalloc을 켜면 상시 오버헤드"
    assert "tracemalloc.start(" in body


def test_live_dataframe_count_reported():
    r = asyncio.run(app.debug_memory())
    lv = r["live_dataframes"]
    assert "error" not in lv, lv
    assert isinstance(lv["count"], int) and lv["count"] >= 0
