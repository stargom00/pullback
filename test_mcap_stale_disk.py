"""v5.285 — 시총 허용목록의 디스크 영속 + stale_disk 폴백.

[사고] 2026-09-24 10:11 NZST 재배포 → 10:12 스케줄러 KR 스캔이 허용목록
0건으로 돌아 시총 1000억 필터가 **꺼진 채** 1,505종목을 스캔했고, 1분 44초
뒤에야 1,858종목으로 충전됐다. 원인은 저장소가 프로세스 메모리 하나뿐
(`_mcap_allowed_cache`)이고 슬롯키가 다르면 직전 목록을 버리는 구조 —
**재배포 직후 첫 KR 스캔은 구조적으로 fail-open**이었다(확정).

[수정] 성공 목록을 /data에 남기고 기동 시 로드. 이번 슬롯이 아직 미충전이면
직전 목록으로 필터를 **적용하되** source를 "stale_disk"로 구분한다 —
필터가 통째로 꺼지는 fail_open과 같은 값으로 뭉개면 화면·로그에서 위험도가
구분되지 않는다.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import app  # noqa: E402


@pytest.fixture
def clean(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_disk_cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr(app, "_mcap_allowed_cache", {}, raising=False)
    monkeypatch.setattr(app, "_mcap_last_good", {}, raising=False)
    return tmp_path


def test_save_writes_state_file_and_load_restores_it(clean, monkeypatch):
    app._save_mcap_allowed("20260924_eod", {"005930.KS", "000660.KS"})
    state = json.loads((clean / app._MCAP_STATE_FILE).read_text(encoding="utf-8"))
    assert state["slotkey"] == "20260924_eod"
    assert sorted(state["tickers"]) == ["000660.KS", "005930.KS"]
    assert state["saved_at"]

    # 재시작 시뮬레이션 — 메모리를 비우고 디스크에서만 복원
    monkeypatch.setattr(app, "_mcap_last_good", {}, raising=False)
    restored = app._load_mcap_allowed_from_disk()
    assert restored["n"] == 2
    assert app._mcap_last_good["tickers"] == {"005930.KS", "000660.KS"}


def test_stale_disk_is_used_when_this_slot_is_not_charged(clean, monkeypatch):
    """**핵심**: 재배포 직후(이번 슬롯 미충전)에도 필터가 켜져야 한다."""
    app._save_mcap_allowed("20260923_eod", {"005930.KS"})
    monkeypatch.setattr(app, "_kr_cache_slot", lambda: "20260924_eod")
    allowed, source = app._get_mcap_allowed_with_source()
    assert source == "stale_disk", source
    assert allowed == {"005930.KS"}
    assert app._kr_mcap_filter_info() == {
        "kr_mcap_filter_source": "stale_disk", "kr_mcap_allowed_count": 1}


def test_fresh_slot_wins_over_stale(clean, monkeypatch):
    app._save_mcap_allowed("20260923_eod", {"005930.KS"})
    monkeypatch.setattr(app, "_kr_cache_slot", lambda: "20260924_eod")
    app._mcap_allowed_cache.update({"slotkey": "20260924_eod", "tickers": {"000660.KS", "035420.KS"}})
    allowed, source = app._get_mcap_allowed_with_source()
    assert source == "mobile_api"
    assert allowed == {"000660.KS", "035420.KS"}


def test_fail_open_only_when_nothing_anywhere(clean, monkeypatch):
    """목록이 **전혀** 없을 때만 fail_open — 이게 유일한 필터 해제 조건이다."""
    monkeypatch.setattr(app, "_kr_cache_slot", lambda: "20260924_eod")
    allowed, source = app._get_mcap_allowed_with_source()
    assert source == "fail_open" and allowed == set()


def test_empty_fresh_cache_does_not_shadow_the_stale_list(clean, monkeypatch):
    """이번 슬롯 키는 맞는데 목록이 빈 경우(부분 실패 등)에도 직전 목록으로
    내려가야 한다 — 슬롯키만 보고 빈 집합을 반환하면 폴백이 죽는다."""
    app._save_mcap_allowed("20260923_eod", {"005930.KS"})
    monkeypatch.setattr(app, "_kr_cache_slot", lambda: "20260924_eod")
    app._mcap_allowed_cache.update({"slotkey": "20260924_eod", "tickers": set()})
    allowed, source = app._get_mcap_allowed_with_source()
    assert source == "stale_disk" and allowed == {"005930.KS"}


def test_single_source_of_truth_for_the_filter_verdict():
    """판정 사본 금지 — TIMING도 /api/calendar도 같은 함수를 거쳐야 한다."""
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert src.count('"kr_mcap_filter_source": _mcap_source') == 1
    # 옛 리터럴 삼항식(mobile_api if ... else fail_open)이 되살아나면 stale_disk가
    # 조용히 사라진다 — 실행 코드 영역에서 0건이어야 한다.
    assert '"mobile_api" if _mcap_allowed else "fail_open"' not in src
