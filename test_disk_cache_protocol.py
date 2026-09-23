"""v5.270/v5.277 — 디스크 캐시 pickle 저장 방식.

v5.270: Python 3.13의 `pickle.DEFAULT_PROTOCOL`은 **4**라, 명시하지 않으면 4로
쓴다. 5는 out-of-band 버퍼를 써서 저장 중 피크가 준다.

v5.277: 번들을 통째로 쓰지 않고 **종목별로 나눠** 쓴다. 같은 조건 A/B(1,500종목
× 1,275봉, 프로세스 격리, **읽기까지 확인**): 통째 **+36MB** → 청크 **+20MB**.
⚠️ 처음엔 "청크 +0MB"로 보고했는데 **틀렸다.** 그때는 간이 스니펫으로 저장
피크만 쟀고 ① 기준선에 번들 생성 피크가 이미 포함돼 저장 비용이 가려졌으며
② **만든 파일을 읽어보지 않았다** — 실제로 그 파일은 `clear_memo()`를 dump
**뒤**에 불러 `UnpicklingError`로 못 읽는 파일이었다. 저장만 재고 넘어가면
못 읽는 형식을 "최적"이라 부르게 된다.

이 파일이 지키는 것:
  1. **저장 → 로드 왕복이 동일한가.** DataFrame은 `==` 비교가 안 되니
     값·dtype·인덱스까지 본다. 이게 깨지면 EOD마다 조용히 캐시가 썩는다.
  2. 실제로 **protocol 5로 쓰였는가**(명시를 지웠는데 테스트가 통과하면 의미 없음).
  3. **로더와 `_CACHE_NS`는 안 건드렸는가** — 이번 변경의 전제다. 로더가
     프로토콜을 자동 인식하므로 바꿀 필요가 없고, 파일 구조가 그대로라
     네임스페이스 범프도 불필요하다.
  4. **구 protocol 4 파일도 계속 읽히는가** — 배포 순간 볼륨에 남아 있는
     기존 캐시가 못 읽히면 그날 EOD가 통째로 콜드 fetch가 된다.
"""
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402


def _bundle(n_tickers=3, n_bars=40):
    data = {}
    for i in range(n_tickers):
        c = np.random.rand(n_bars).astype("float32") * 100
        data[f"{i:06d}.KS"] = pd.DataFrame(
            {"Open": c, "High": c, "Low": c, "Close": c,
             "Volume": (c * 1000).astype("float32")},
            index=pd.bdate_range("2024-01-01", periods=n_bars))
    return {
        "universe": {t: f"종목{t}" for t in data}, "data": data,
        "data_ts": {t: 1_758_000_000.0 for t in data},
        "rs_ranks": {t: 50 for t in data}, "rs_moms": {t: 1.0 for t in data},
        "rs3_ranks": {t: 50 for t in data}, "rs_deltas": {t: 0.0 for t in data},
        "sector_info": {"by_ticker": {}}, "ts": 1_758_000_000.0,
        "daykey": "2026-09-20",
        # timing은 `_TIMING_SCHEMA_KEYS`를 **전부** 채워야 로더의 스키마 검증을
        # 통과한다(v5.241). 빈 dict로 뒀다가 전 테스트가 "못 읽는다"로 떨어졌다
        # — 검증이 살아 있다는 증거였고, 픽스처가 틀렸던 것이다.
        # 키 목록을 여기 복사하지 않고 app에서 읽어와 동기화가 깨질 여지를 없앤다.
        "timing": {k: 0 for k in app._TIMING_SCHEMA_KEYS},
    }


@pytest.fixture
def cache_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "_disk_cache_dir", lambda: str(tmp_path))
    return tmp_path


def _assert_same(a: dict, b: dict):
    assert set(a) == set(b), (set(a) ^ set(b))
    for t, df in a["data"].items():
        got = b["data"][t]
        pd.testing.assert_frame_equal(df, got, check_exact=True)
    for k in ("universe", "rs_ranks", "rs_moms", "rs3_ranks", "rs_deltas",
              "data_ts", "ts", "daykey"):
        assert a[k] == b[k], k


def test_round_trip_is_identical(cache_dir):
    """저장한 것과 읽은 것이 값·dtype·인덱스까지 같아야 한다."""
    src = _bundle()
    app._save_disk_cache("kr", "2026-09-20", src)
    got = app._load_disk_cache("kr", "2026-09-20")
    assert got is not None, "저장한 캐시를 못 읽는다"
    _assert_same(src, got)


def test_float32_dtype_survives(cache_dir):
    """dtype이 float64로 부풀면 메모리가 2배가 된다 — 조용히 일어날 수 있다."""
    src = _bundle()
    app._save_disk_cache("kr", "2026-09-20", src)
    got = app._load_disk_cache("kr", "2026-09-20")
    for t, df in got["data"].items():
        for col in ("Open", "High", "Low", "Close", "Volume"):
            assert str(df[col].dtype) == "float32", (t, col, df[col].dtype)


def test_file_is_actually_protocol_5(cache_dir):
    """명시를 지워도 왕복 테스트는 통과한다 — 프로토콜 자체를 확인한다.

    pickle 파일의 두 바이트는 PROTO opcode(0x80) + 버전이다.
    """
    app._save_disk_cache("kr", "2026-09-20", _bundle())
    raw = Path(app._disk_cache_path("kr", "2026-09-20")).read_bytes()
    assert raw[0] == 0x80, "pickle PROTO 헤더가 아니다"
    assert raw[1] == 5, f"protocol {raw[1]}로 저장됐다 — protocol=5 명시가 빠졌다"


def test_source_specifies_the_protocol():
    src = Path(app.__file__).read_text(encoding="utf-8")
    i = src.index("def _save_disk_cache")
    body = src[i:i + 1500]
    assert "protocol=5" in body, "protocol 명시가 사라졌다"


def test_loader_was_not_changed():
    """이번 변경의 전제 — 로더는 그대로다(pickle.load가 프로토콜 자동 인식)."""
    src = Path(app.__file__).read_text(encoding="utf-8")
    i = src.index("def _load_disk_cache")
    body = src[i:src.index("def _save_disk_cache")]
    assert "pickle.load(f)" in body, "로더가 바뀌었다"
    assert "protocol" not in body, "로더에 프로토콜 분기가 생겼다 — 불필요하다"


def test_cache_namespace_moved_for_the_format_change():
    """v5.277은 **파일 구조가 실제로 바뀌었다** — v5.270(protocol만 변경)과
    달리 네임스페이스를 올려야 한다."""
    assert app._CACHE_NS == "rs9", app._CACHE_NS


def test_old_whole_bundle_files_still_load(cache_dir):
    """구 형식(통째 dump)도 읽혀야 한다 — 네임스페이스를 올렸으니 사실상 안
    만나지만, 볼륨에 남은 파일로 500이 나면 안 된다."""
    for proto in (4, 5):
        src = _bundle()
        path = app._disk_cache_path("kr", "2026-09-20")
        with open(path, "wb") as f:
            pickle.dump(src, f, protocol=proto)
        got = app._load_disk_cache("kr", "2026-09-20")
        assert got is not None, f"구 protocol {proto} 캐시를 못 읽는다"
        _assert_same(src, got)


def test_chunked_file_has_the_marker_and_one_object_per_ticker(cache_dir):
    """형식 자체를 고정 — 헤더 1개 + 종목 수만큼의 객체."""
    src = _bundle(n_tickers=4)
    app._save_disk_cache("kr", "2026-09-20", src)
    with open(app._disk_cache_path("kr", "2026-09-20"), "rb") as f:
        head = pickle.load(f)
        assert head.get("__chunked__") == 4, head.get("__chunked__")
        assert "data" not in head, "헤더에 data가 통째로 들어갔다"
        seen = [pickle.load(f) for _ in range(4)]
    assert sorted(k for k, _ in seen) == sorted(src["data"]), seen


def test_memo_is_cleared_before_each_chunk(cache_dir):
    """`clear_memo()`를 dump **뒤**에 부르면 첫 청크가 헤더의 메모를 참조해
    `UnpicklingError`가 난다 — 작성 중 실제로 그랬고, 저장 피크만 재느라
    **못 읽는 파일을 최적이라 부를 뻔했다**."""
    src = _bundle(n_tickers=3)
    app._save_disk_cache("kr", "2026-09-20", src)
    got = app._load_disk_cache("kr", "2026-09-20")
    assert got is not None, "메모 순서가 틀려 못 읽는다"
    _assert_same(src, got)


def test_saving_does_not_mutate_the_caller_bundle(cache_dir):
    """`__chunked__`가 원본에 남으면 메모리 캐시가 오염된다."""
    src = _bundle()
    app._save_disk_cache("kr", "2026-09-20", src)
    assert "__chunked__" not in src
    assert "data" in src and len(src["data"]) == 3


def test_schema_validation_still_runs(cache_dir):
    """v5.241 스키마 검증이 프로토콜 변경으로 무력화되지 않았는가."""
    bad = _bundle()
    del bad["rs_ranks"]
    app._save_disk_cache("kr", "2026-09-20", bad)
    assert app._load_disk_cache("kr", "2026-09-20") is None, "깨진 스키마가 통과했다"


# ══════════════════════════════════════════════════════════════════════
# v5.277 — EOD 직후 메모리 반환
# ══════════════════════════════════════════════════════════════════════
def test_release_memory_never_runs_on_the_event_loop():
    """**v5.278 교정.** v5.277은 루프에서 직접 불러 정리 몇 초 동안 홈·API가
    전부 멈췄다(실측: /api/debug/memory 11.2초, 한 번은 45초 타임아웃).
    `gc.collect()`는 수백 MB 힙에서 수 초가 걸린다 — executor로 빼야 한다."""
    import inspect
    assert inspect.iscoroutinefunction(app._release_memory), "동기 함수로 되돌아갔다"
    src = inspect.getsource(app._release_memory)
    assert "run_in_executor" in src, "루프에서 직접 돈다"
    assert not inspect.iscoroutinefunction(app._release_memory_blocking)
    # 호출부가 await 하는지 — 안 하면 코루틴이 안 돌고 경고만 뜬다
    full = Path(app.__file__).read_text(encoding="utf-8")
    i = full.index('_release_memory(f"EOD')
    assert full[i - 6:i].strip().endswith("await"), full[i - 40:i + 40]


def test_release_memory_is_called_at_the_end_of_eod():
    src = Path(app.__file__).read_text(encoding="utf-8")
    i = src.index("async def _warm_market")
    body = src[i:i + 9000]
    assert "_release_memory(" in body, "EOD 끝에서 안 부른다"
    # 예외 처리 **밖**이어야 한다 — 워밍이 실패해도 정리는 돌아야 한다
    j = body.index("_release_memory(")
    k = body.index('print(f"[scheduler] warm {market} failed')
    assert j > k, "실패 처리 안쪽에 있다 — 성공 경로에서 안 돈다"


def test_release_memory_survives_without_glibc():
    """개발 머신(macOS)엔 `malloc_trim`이 없다. 여기서 예외가 나면 EOD가 죽는다."""
    app._release_memory_blocking("테스트")   # 예외 없이 끝나면 통과


def test_release_memory_reports_which_path_ran(capsys):
    """조용히 지나가면 프로덕션에서 **실제로 trim이 됐는지** 알 수 없다.

    v5.282에서 포맷이 바뀌었다(`malloc_trim …` → `trim=0/1` 또는 `미지원`) —
    문구가 아니라 **결과가 드러나는가**를 본다.
    """
    app._release_memory_blocking("EOD kr")
    out = capsys.readouterr().out
    assert "[mem] EOD kr" in out and "gc" in out, out
    assert ("trim=0" in out or "trim=1" in out or "미지원" in out), out


def test_release_memory_swallows_a_hostile_ctypes(monkeypatch):
    """libc가 있는데 malloc_trim이 없는 환경(musl 등)에서도 죽지 않아야 한다."""
    import ctypes

    class NoTrim:
        def __getattr__(self, name):
            raise AttributeError(name)

    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: NoTrim())
    app._release_memory_blocking("musl")            # 예외 없이 끝나면 통과


def test_gc_runs_even_when_trim_is_unavailable(monkeypatch):
    import ctypes
    calls = []
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: (_ for _ in ()).throw(OSError("no libc")))
    import gc as _gc
    real = _gc.collect
    monkeypatch.setattr(_gc, "collect", lambda *a: (calls.append(1), real())[1])
    app._release_memory_blocking("no-libc")
    assert calls, "malloc_trim이 없다고 gc까지 건너뛴다"


# ══════════════════════════════════════════════════════════════════════
# v5.282 — `[mem]` 로그에 RSS 3점 (사용자 지시)
# ══════════════════════════════════════════════════════════════════════
# 예전 로그는 "gc N개"뿐이라 **실제로 메모리가 돌아왔는지** 알 수 없었다.
# 특히 `malloc_trim`이 Linux에서 진짜 잡히는지 판단할 근거가 없었다.
# 소스는 `_rss_mb()` — `/api/debug/memory`와 같아 memory_probe 기록과 대조된다.

def _mem_line(capsys, tag="EOD kr") -> str:
    app._release_memory_blocking(tag)
    out = [l for l in capsys.readouterr().out.splitlines() if l.startswith("[mem]")]
    assert out, "로그가 안 찍혔다"
    return out[-1]


def test_log_has_three_rss_points_in_order(capsys):
    line = _mem_line(capsys)
    assert "rss" in line and "→ gc" in line and "→ trim" in line, line
    assert line.index("rss") < line.index("→ gc") < line.index("→ trim"), line


def test_the_three_points_are_measured_separately(capsys):
    """세 값이 **각각** 측정돼야 한다 — 같은 변수를 세 번 쓰면 무의미하다."""
    import inspect
    src = inspect.getsource(app._release_memory_blocking)
    # 주석에도 `_rss_mb()`가 적혀 있어 통째로 세면 4가 된다(작성 중 겪음) —
    # **대입문만** 센다.
    import re
    assigns = re.findall(r"rss_[abc] = _rss_mb\(\)", src)
    assert len(assigns) == 3, f"측정 대입이 3회가 아니다: {assigns}"
    # 순서 검사는 **실행 코드에서만** — docstring에도 `gc.collect()`가 적혀
    # 있어 통째로 index()하면 본문보다 앞선다(작성 중 겪음).
    body = src[src.index("    import gc"):]
    order = [body.index(k) for k in ("rss_a = _rss_mb()", "freed = gc.collect()",
                                     "rss_b = _rss_mb()", "malloc_trim(0)",
                                     "rss_c = _rss_mb()")]
    assert order == sorted(order), f"측정 순서가 어긋났다: {order}"
    # **출력에 세 값이 각각 들어가는지**까지 본다 — 측정만 하고 `rss_c` 대신
    # `rss_b`를 찍으면 위 검사는 전부 통과한다(사용자가 지시한 사보타주 CL이
    # 실제로 그렇게 빠져나갔다).
    fmt = body[body.index('print(f"[mem]'):]
    for var in ("rss_a", "rss_b", "rss_c"):
        assert f"_f({var})" in fmt, f"{var}가 출력에 안 쓰인다: {fmt[:200]}"
    assert fmt.count("_f(rss_b)") == 1, "같은 값을 두 자리에 찍는다"
    assert fmt.count("_f(rss_c)") == 1


def test_trim_flag_is_zero_or_one_or_unsupported(capsys):
    """`trim=0/1`로 **반환값**이 드러나야 한다 — 호출 여부가 아니라 결과."""
    line = _mem_line(capsys)
    assert ("trim=0" in line or "trim=1" in line or "미지원" in line), line


def test_uses_the_same_rss_source_as_the_probe():
    """memory_probe가 읽는 `/api/debug/memory`와 같은 함수여야 대조가 된다."""
    import inspect
    assert "_rss_mb()" in inspect.getsource(app._release_memory_blocking)
    assert "VmRSS" in inspect.getsource(app._rss_mb)


def test_gc_count_is_still_reported(capsys):
    line = _mem_line(capsys)
    assert "gc" in line and "개" in line, line


def test_tag_is_in_the_line(capsys):
    assert "EOD us" in _mem_line(capsys, "EOD us")


def test_missing_rss_does_not_crash(capsys, monkeypatch):
    """`/proc` 없고 resource도 실패하면 None이 온다 — 포맷이 죽으면 안 된다."""
    monkeypatch.setattr(app, "_rss_mb", lambda: None)
    line = _mem_line(capsys)
    assert "?" in line, line
