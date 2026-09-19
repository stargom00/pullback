"""v5.270 — 디스크 캐시 pickle protocol=5.

Python 3.13의 `pickle.DEFAULT_PROTOCOL`은 **4**라, 명시하지 않으면 4로 쓴다.
5는 out-of-band 버퍼를 써서 저장 중 피크가 크게 준다 — 실측(1,500종목 ×
1,275봉, 53.5MB 번들, 프로세스 격리): peak 증가 **+80MB → +21MB**.

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


def test_cache_namespace_was_not_bumped():
    """파일 구조가 안 바뀌므로 범프 불필요 — 범프했다면 그날 EOD가 콜드가 된다."""
    assert app._CACHE_NS == "rs8", (
        f"_CACHE_NS가 {app._CACHE_NS}로 바뀌었다 — protocol 변경만으로는 범프하지 않는다")


def test_old_protocol_4_files_still_load(cache_dir):
    """배포 순간 볼륨에 남아 있는 구 캐시가 못 읽히면 EOD가 통째로 콜드가 된다."""
    src = _bundle()
    path = app._disk_cache_path("kr", "2026-09-20")
    with open(path, "wb") as f:
        pickle.dump(src, f, protocol=4)          # 배포 전에 쓰인 형태
    got = app._load_disk_cache("kr", "2026-09-20")
    assert got is not None, "구 protocol 4 캐시를 못 읽는다"
    _assert_same(src, got)


def test_schema_validation_still_runs(cache_dir):
    """v5.241 스키마 검증이 프로토콜 변경으로 무력화되지 않았는가."""
    bad = _bundle()
    del bad["rs_ranks"]
    app._save_disk_cache("kr", "2026-09-20", bad)
    assert app._load_disk_cache("kr", "2026-09-20") is None, "깨진 스키마가 통과했다"
