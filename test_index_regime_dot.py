"""v5.257 상단 지수 신호등 — 데이터 없을 때 색을 내지 않는가, 게이트와 일치하는가.

[버그] ix에 regime 키가 없어도(서버 _index_regime이 None → 병합 안 됨)
gateOf()의 else 분기가 무조건 'neutral 🟡 지수 혼조'를 만들어냈다.
또 지수 카드의 점은 regime을 그대로 매핑해(dist_days 무시) 같은 지수인데
배너 🟡 / 점 🟢으로 갈렸다.

프론트 함수는 loadIndices() 클로저 안에 있어 최상위 추출이 안 되므로,
소스에서 해당 const 선언만 잘라내 node로 실행한다(재구현 금지 원칙은 동일 —
production 텍스트 그 자체를 돌린다).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

IDX = Path(__file__).resolve().parent / "static" / "index.html"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node 미설치")


def extract_const_arrow(name: str) -> str:
    """`const <name> = (...) => { ... };` 를 중괄호 깊이로 잘라낸다."""
    src = IDX.read_text(encoding="utf-8")
    start = src.index(f"const {name} = (")
    i = src.index("{", src.index("=>", start))
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1] + ";"
    raise AssertionError(f"{name}: 닫는 중괄호를 못 찾음")


def extract_function(name: str) -> str:
    src = IDX.read_text(encoding="utf-8")
    start = src.index(f"function {name}(")
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"{name}: 닫는 중괄호를 못 찾음")


def _prelude() -> str:
    src = IDX.read_text(encoding="utf-8")
    dot_map = [l for l in src.splitlines() if "const GATE_LV_DOT" in l][0]
    return "\n".join([
        extract_function("idxStaleNote"),
        [l for l in src.splitlines() if l.strip().startswith("const IDX_STALE_DAYS")][0],
        extract_const_arrow("gateOf"),
        dot_map,
        extract_const_arrow("regimeDot"),
    ])


def run(ixs):
    src = _prelude() + f"\nconst ixs={json.dumps(ixs)};" + (
        "console.log(JSON.stringify(ixs.map(ix=>{const g=gateOf(ix);"
        "return {lv: g && g.lv, html: g && g.html, dot: regimeDot(ix)};})));")
    out = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_no_color_without_regime():
    """핵심 회귀: regime이 없으면 🟡을 지어내지 않는다."""
    got = run([{"name": "나스닥"}, {"name": "코스피", "dist_days": 0}])
    assert [g["lv"] for g in got] == ["unknown", "unknown"]
    assert all(g["dot"] == "⚪" for g in got)
    assert all("데이터 대기" in g["html"] for g in got)


def test_missing_index_returns_null():
    got = run([None])
    assert got[0]["lv"] is None and got[0]["dot"] == ""


def test_dot_matches_gate_level_always():
    """배너와 카드 점이 갈리면 안 된다(같은 지수, 같은 판정)."""
    cases = [
        {"regime": "good", "dist_days": 0},
        {"regime": "good", "dist_days": 4},    # 이전 버그: 배너 🟡 / 점 🟢
        {"regime": "neutral", "dist_days": 2},
        {"regime": "bad", "dist_days": 7},
        {"regime": "good", "dist_days": None},
        {},
    ]
    got = run(cases)
    want = {"good": "🟢", "neutral": "🟡", "bad": "🔴", "unknown": "⚪"}
    for g in got:
        assert g["dot"] == want[g["lv"]], g


def test_regime_good_with_many_dist_is_neutral():
    got = run([{"regime": "good", "dist_days": 4}])
    assert got[0]["lv"] == "neutral" and got[0]["dot"] == "🟡"


def test_dist_unknown_is_flagged_not_treated_as_zero():
    """거래량 없어 분산일 판정 불가(None)를 0으로 위장하면 '건강'이 된다."""
    got = run([{"regime": "good", "dist_days": None}])
    assert "분산일 판정 불가" in got[0]["html"]
    assert "분산일 0개" not in got[0]["html"]


def test_stale_note_only_when_old():
    import datetime as dt
    today = dt.date.today().isoformat()
    old = (dt.date.today() - dt.timedelta(days=10)).isoformat()
    recent = (dt.date.today() - dt.timedelta(days=2)).isoformat()
    got = run([
        {"regime": "good", "dist_days": 0, "last_bar": today},
        {"regime": "good", "dist_days": 0, "last_bar": recent},
        {"regime": "good", "dist_days": 0, "last_bar": old},
    ])
    assert "마지막 봉" not in got[0]["html"]
    assert "마지막 봉" not in got[1]["html"], "주말 정도는 경고하지 않는다"
    assert f"마지막 봉 {old}" in got[2]["html"]


def test_unknown_has_grey_css_class():
    """색을 안 내려면 mkt-unknown 스타일이 실제로 있어야 한다."""
    src = IDX.read_text(encoding="utf-8")
    assert ".mkt-banner.mkt-unknown{" in src


def test_server_exposes_last_bar():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import app
    body = Path(app.__file__).read_text(encoding="utf-8")
    i = body.index("def _index_regime")
    assert '"last_bar": str(close.index[-1].date())' in body[i:i + 6000]
