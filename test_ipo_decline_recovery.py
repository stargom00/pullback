"""상장 후 하락 회복 탐색(docs/ipo_decline_recovery_exploration.md) — 패턴 판정·신호 미래 참조·분할 고정."""
from __future__ import annotations

import importlib.util
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("ipo", os.path.join(ROOT, "scripts", "measurements", "2026-10-09_ipo_decline_recovery_exploration.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def _df(closes, opens=None, vol=None):
    c = np.asarray(closes, float)
    o = np.asarray(opens if opens is not None else c, float)
    idx = pd.bdate_range("2020-01-02", periods=len(c))
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * 1.01, "Low": np.minimum(o, c) * 0.99, "Close": c,
                         "Volume": np.asarray(vol if vol is not None else np.full(len(c), 1e6), float)}, index=idx)


def _series(post=300, rec=True):
    """첫 20봉 200(제외 구간 — 고점 후보 아님), 21~40봉 100(상장 고점 100), 41~100봉 100 → 50 하락, 저점 50(101번째),
    이후 rec면 80까지 상승(≥ 75 = 회복), 아니면 55 근처."""
    c = [200.0] * 20 + [100.0] * 20 + list(np.linspace(100, 50, 61)) + list(np.linspace(51, 80 if rec else 55, post))
    return c


def test_pattern_peak_trough_recovery():
    s = _df(_series())
    pt = m.pattern(s)
    c = s["Close"].values
    assert pt["declined"] and pt["t"] == 100 and c[pt["t"]] == 50 and c[pt["p"]] == 100 and pt["p"] >= 20   # 첫 20봉(200)은 고점 아님
    assert pt["group"] == "recovered" and c[pt["rec"]] >= 75 and c[pt["rec"] - 1] < 75
    assert m.pattern(_df(_series(rec=False)))["group"] == "unrecovered"
    assert m.pattern(_df(_series(post=100, rec=False)))["group"] == "pending"               # 저점 이후 250봉 미만


def test_not_declined_when_drop_less_than_40pct():
    c = [100.0] * 30 + list(np.linspace(100, 61, 50)) + [70.0] * 300
    assert m.pattern(_df(c)) == {"declined": False}


def _signal_fixture():
    """하락 300봉(100 → 40) → 바닥 120봉(40 ± 1) → 진동하며 상승 400봉 — 다섯 신호가 모두 한 번 이상 켜지는 결정적 데이터."""
    j = np.arange(400)
    return (list(np.linspace(100, 40, 300)) + list(40 + np.sin(np.arange(120) / 5)) + list(40 + 0.25 * j + 4 * np.sin(j / 6)))


def test_signals_do_not_look_ahead():
    """미래 참조 검사(봉 단위): 모든 봉 i에서 i 이후를 극단값(0.01·10^6) 두 가지로 바꿔도 0..i의 신호가 같아야 한다.
    처음엔 37봉 간격·무작위 미래로만 봐서 '다음 봉 이동평균'·'반등 고점 창에 미래 포함' 사보타주가 통과했다(테스트 결함, 2026-10-09 수정)."""
    c = _signal_fixture()
    base = m.signal_series(_df(c))
    for k in m.SIGNALS:
        assert base[k].any(), f"픽스처에서 {k}가 한 번도 안 켜짐 — 검사가 무의미"
    for i in range(m.SKIP_FIRST, len(c) - 1):
        for fill in (0.01, 1e6):
            alt = m.signal_series(_df(c[:i + 1] + [fill] * (len(c) - i - 1)))
            for k in m.SIGNALS:
                assert alt[k][i] == base[k][i], (k, i, fill)


def test_forward_returns_and_first_hit():
    c = [100.0] * 10 + [100.0 + i for i in range(300)]
    s = _df(c, opens=[100.0] * 10 + [100.0 + i for i in range(300)])
    f = m.forward(s, 9)                                   # 진입 = 10번째 봉(인덱스 10) 시가 100
    assert f["entry"] == 100.0 and abs(f["r63"] - 62.0) < 1e-9 and abs(f["r250"] - 249.0) < 1e-9
    assert f["first_hit"] == "up30"
    d = [100.0] * 10 + [100.0 - 0.5 * i for i in range(300)]
    assert m.forward(_df(d, opens=d), 9)["first_hit"] == "down20"


def test_split_is_deterministic_and_balanced():
    codes = [f"{i:06d}.KQ" for i in range(2000)]
    ex = [c for c in codes if m.is_explore(c)]
    assert 900 < len(ex) < 1100 and all(m.is_explore(c) == m.is_explore(c.replace(".KQ", ".KS")) for c in codes[:50])


def test_excluded_kinds():
    assert m.excluded_kind("엔에이치스팩29호") == "spac" and m.excluded_kind("하나기업인수목적") == "spac"
    assert m.excluded_kind("롯데리츠") == "reit" and m.excluded_kind("코람코라이프인프라리츠") == "reit"
    assert m.excluded_kind("삼성전자") is None
