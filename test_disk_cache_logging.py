"""v5.285 — 디스크 캐시 계측(hit/miss/스키마불일치/save/기동 목록).

**동작은 바꾸지 않는다.** 2026-09-24 KR 콜드 스캔(n_reused=0) 원인이
"u{N} 불일치인지 어젯밤 저장 실패인지" 미확정인데, `_save_disk_cache`가
`except Exception: pass`라 **실패도 무음**이고 `_load_disk_cache`는 파일이
없으면 조용히 None이라 로그만으론 구분이 불가능했다. 다음 번엔 한 줄로
대조되도록 사실만 남긴다(캐시 미스 자체는 손대지 않는다 — 원인 미확정).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import app  # noqa: E402


@pytest.fixture
def d(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_disk_cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr(app, "_universe_sig", lambda m: "u7")
    return tmp_path


def _bundle():
    b = {k: {} for k in app._BUNDLE_SCHEMA_KEYS}
    b["timing"] = {k: 0 for k in app._TIMING_SCHEMA_KEYS}
    b["data"] = {"005930.KS": {"x": 1}}
    return b


def test_miss_logs_the_wanted_name_and_what_exists(d, capsys):
    (d / "datacache_rs9_kr_u9999_2026-09-23.pkl").write_bytes(b"x")
    assert app._load_disk_cache("kr", "2026-09-23") is None
    out = capsys.readouterr().out
    assert "miss datacache_rs9_kr_u7_2026-09-23.pkl" in out, out
    assert "datacache_rs9_kr_u9999_2026-09-23.pkl" in out, "실제 있는 파일명이 안 찍혔다"


def test_save_then_hit_logs_both(d, capsys):
    app._save_disk_cache("kr", "2026-09-23", _bundle())
    out = capsys.readouterr().out
    assert "saved datacache_rs9_kr_u7_2026-09-23.pkl" in out, out
    assert "MB)" in out

    assert app._load_disk_cache("kr", "2026-09-23") is not None
    assert "hit datacache_rs9_kr_u7_2026-09-23.pkl" in capsys.readouterr().out


def test_save_failure_is_logged_not_swallowed(d, capsys, monkeypatch):
    """**사보타주가 아니라 실사례**: 저장이 죽어도 스캔은 계속돼야 하지만
    (fail-safe) 조용하면 안 된다."""
    def boom(*a, **k):
        raise OSError("no space left on device")
    monkeypatch.setattr(app.os, "replace", boom)
    app._save_disk_cache("kr", "2026-09-23", _bundle())     # 예외가 새어나오면 안 됨
    out = capsys.readouterr().out
    assert "저장 실패" in out and "no space left on device" in out, out


def test_schema_mismatch_still_logs_and_lists(d, capsys):
    bad = _bundle()
    del bad["rs_ranks"]
    app._save_disk_cache("kr", "2026-09-23", bad)
    capsys.readouterr()
    assert app._load_disk_cache("kr", "2026-09-23") is None
    out = capsys.readouterr().out
    assert "스키마 불일치" in out and "현재 kr 파일" in out, out


def test_startup_logs_the_file_list(d, capsys):
    (d / "datacache_rs9_us_u2120_2026-09-24.pkl").write_bytes(b"x")
    app._log_startup_disk_state()
    out = capsys.readouterr().out
    assert "시작 시" in out
    assert "datacache_rs9_us_u2120_2026-09-24.pkl" in out, out


def test_no_silent_except_pass_left_in_save():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    body = src[src.index("def _save_disk_cache("):src.index("def _benchmark_close(")]
    assert "except Exception:\n        pass" not in body, "저장 실패가 다시 무음이 됐다"
