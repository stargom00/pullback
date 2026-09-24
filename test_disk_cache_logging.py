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
    return tmp_path


def _bundle():
    b = {k: {} for k in app._BUNDLE_SCHEMA_KEYS}
    b["timing"] = {k: 0 for k in app._TIMING_SCHEMA_KEYS}
    b["data"] = {"005930.KS": {"x": 1}}
    return b


def test_miss_logs_the_wanted_name_and_what_exists(d, capsys):
    (d / "datacache_rs9_us_2026-09-23.pkl").write_bytes(b"x")   # 다른 market
    assert app._load_disk_cache("kr", "2026-09-23") is None
    out = capsys.readouterr().out
    assert "miss datacache_rs9_kr_2026-09-23.pkl" in out, out


def test_save_logs_name_and_size(d, capsys):
    app._save_disk_cache("kr", "2026-09-23", _bundle())
    out = capsys.readouterr().out
    assert "saved datacache_rs9_kr_2026-09-23.pkl" in out, out
    assert "MB)" in out
    assert app._load_disk_cache("kr", "2026-09-23") is not None


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
    (d / "datacache_rs9_us_2026-09-24.pkl").write_bytes(b"x")
    app._log_startup_disk_state()
    out = capsys.readouterr().out
    assert "시작 시" in out
    assert "datacache_rs9_us_2026-09-24.pkl" in out, out


# ── v5.286: 기동 시 구 네임스페이스 정리 ────────────────────────────────
# 09-25 기동 로그에서 몇 달 묵은 파일 4개가 확인됐다 — 저장 시 정리는 그
# market을 실제로 저장할 때만 도는데 "all" 같은 은퇴한 market 이름은 이제
# 저장될 일이 없어 영원히 안 지워진다.
OLD_FILES = ["datacache_all_2026-06-23.pkl", "datacache_kr_2026-06-23.pkl",
             "datacache_rs6_all_u3625_2026-09-09.pkl", "datacache_us_2026-06-24.pkl"]
CURRENT_FILES = ["datacache_rs9_kr_2026-09-23.pkl", "datacache_rs9_us_2026-09-24.pkl",
                 "datacache_rs9_kr_u1504_2026-09-23.pkl"]   # 전환기 구 이름도 현재 NS다


def test_startup_deletes_only_retired_namespaces(d, capsys):
    for fn in OLD_FILES + CURRENT_FILES:
        (d / fn).write_bytes(b"x")
    app._log_startup_disk_state()
    left = sorted(f.name for f in d.glob("datacache_*.pkl"))
    assert left == sorted(CURRENT_FILES), left
    out = capsys.readouterr().out
    assert "구 네임스페이스 파일 4개 삭제" in out, out
    for fn in OLD_FILES:
        assert fn in out


def test_startup_never_touches_current_namespace_files(d, capsys):
    """**오늘 쓸 캐시를 기동이 지우면 그게 바로 전량 콜드다.**"""
    for fn in CURRENT_FILES:
        (d / fn).write_bytes(b"x")
    app._log_startup_disk_state()
    assert sorted(f.name for f in d.glob("datacache_*.pkl")) == sorted(CURRENT_FILES)
    assert "구 네임스페이스 파일 0개 삭제" in capsys.readouterr().out


def test_no_silent_except_pass_left_in_save():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    body = src[src.index("def _save_disk_cache("):src.index("def _benchmark_close(")]
    assert "except Exception:\n        pass" not in body, "저장 실패가 다시 무음이 됐다"
