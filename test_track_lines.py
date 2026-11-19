"""v5.386 추적 장부 — track_lines(값·검증) + /api/track-lines(옮겨 심기·rev·삭제 로그·보기 필드).

사보타주 확인(2026-11-19, FAIL 확인 후 원복):
① line_value의 linear 분기 제거(전부 로그) → test_shared_vectors FAIL
② track_line_put이 kind를 받은 값 그대로 저장 → test_put_recomputes_kind_and_validates FAIL
③ _track_lines_load가 옮겨 심은 뒤 파일을 안 씀 → test_seed_once_then_file_is_source FAIL
④ view_row의 near 판정을 < 로(경계 제외) → test_view_zones FAIL
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import json
import os

import pytest

import app
import track_lines as tl

ROOT = os.path.dirname(os.path.abspath(__file__))


class _Req:
    def __init__(self, body=None):
        self._body = body
        self.headers = {"user-agent": "pytest"}
        self.client = type("C", (), {"host": "127.0.0.1"})()

    async def json(self):
        return self._body


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "TRACK_LINES_PATH", str(tmp_path / "track_lines.json"))
    monkeypatch.setattr(app, "TRACK_LINES_DELETE_LOG_PATH", str(tmp_path / "track_lines_deletions.log"))
    monkeypatch.setattr(app, "_track_close", lambda t: ({"352820.KS": 189400.0, "NVDA": 165.0}.get(t), "2026-11-19") if t in ("352820.KS", "NVDA") else (None, None))
    return tmp_path


def _put(rec, base):
    r = asyncio.run(app.track_line_put(rec["id"], _Req({"record": rec, "base_rev": base})))
    return r.status_code, json.loads(r.body)


def _list(prices=1):
    return json.loads(asyncio.run(app.track_lines_list(prices)).body)


HYBE = {"id": "tl_hybe_top", "ticker": "352820.KS", "name": "하이브", "theme": "한한령", "label": "채널 상단", "scale": "log",
        "p1": ["2026-06-25", 250000], "p2": ["2026-10-15", 180000], "note": "상단 종가 돌파 시 진입 검토", "status": "추적"}


def test_shared_vectors():
    """얼마냐봇 trendlines.line_value와 같은 값(두 레포 테스트에 같은 숫자 — stock-alert test_trendlines.py::test_shared_vectors)."""
    log = {"id": "a", "p1": ["2026-01-01", 100.0], "p2": ["2026-04-11", 200.0]}            # 100일
    assert abs(tl.line_value(log, "2026-02-20") - 100 * 2 ** 0.5) < 1e-9                    # 50일째 = √2배
    assert abs(tl.line_value(log, "2026-07-20") - 400.0) < 1e-9                             # 200일째(연장) = 4배
    lin = dict(log, scale="linear")
    assert abs(tl.line_value(lin, "2026-02-20") - 150.0) < 1e-9
    assert abs(tl.line_value(lin, "2026-07-20") - 300.0) < 1e-9
    with pytest.raises(ValueError):
        tl.line_value({"id": "x", "p1": ["2026-01-01", 1], "p2": ["2026-01-01", 2]}, "2026-01-02")


def test_kind_and_validation():
    assert tl.kind_of(HYBE) == "fall" and tl.kind_of(dict(HYBE, p2=["2026-10-15", 250000])) == "flat"
    assert tl.kind_of(dict(HYBE, p1=["2026-10-15", 180000], p2=["2026-06-25", 250000])) == "fall"   # 점 순서가 뒤집혀도 시간순으로
    assert tl.invalid(HYBE) is None
    for bad in (dict(HYBE, ticker="352820.ks"), dict(HYBE, name=""), dict(HYBE, label="대각선"), dict(HYBE, scale="ln"),
                dict(HYBE, p1=["2026-06-25", 0]), dict(HYBE, p1=["2026-6-25", 1]), dict(HYBE, p2=["2026-06-25", 180000]),
                dict(HYBE, p1=["2026-06-25", True]), dict(HYBE, note="x" * 301), dict(HYBE, view={}), dict(HYBE, status="보류")):
        assert tl.invalid(bad), bad


def test_view_zones():
    base = {"id": "f", "p1": ["2026-01-01", 100.0], "p2": ["2026-06-01", 100.0]}
    z = lambda c: tl.view_row(base, c, "2026-11-19")["zone"]
    assert [z(103.0), z(97.0), z(103.1), z(96.9)] == ["near", "near", "above", "below"]     # ±3% 경계 포함
    up = tl.view_row(base, 80.0, "2026-11-19")                       # 선이 가격 위 → 선까지 +25%, 가격은 선 아래 −20%
    assert abs(up["to_line_pct"] - 25.0) < 1e-9 and abs(up["dist_pct"] + 20.0) < 1e-9 and up["zone"] == "below"
    v = tl.view_row(base, None, "2026-11-19")
    assert v["zone"] == "unknown" and v["dist_pct"] is None and v["to_line_pct"] is None and v["value"] == 100.0


def test_seed_once_then_file_is_source(store):
    seed = json.load(open(os.path.join(ROOT, "track_lines_seed.json"), encoding="utf-8"))
    j = _list(0)
    assert j["ok"] and j["n"] == len(seed) == 19
    assert {x["id"] for x in j["lines"]} == {x["id"] for x in seed}
    assert all(x["rev"] == 1 and x["status"] == "추적" and x["scale"] == "log" for x in j["lines"])
    assert {x["label"] for x in j["lines"]} == {"빗각", "수평선"}
    assert os.path.exists(app.TRACK_LINES_PATH)
    # 하나 지우면 다시 안 살아난다(옮겨 심기는 파일이 없을 때 한 번뿐)
    r = asyncio.run(app.track_line_delete("soxx-flat-165", _Req()))
    assert json.loads(r.body)["deleted"] == "soxx-flat-165"
    assert _list(0)["n"] == 18 and "soxx-flat-165" in open(app.TRACK_LINES_DELETE_LOG_PATH, encoding="utf-8").read()


def test_put_recomputes_kind_and_validates(store):
    _list(0)
    st, j = _put(dict(HYBE, kind="rise"), None)                       # 받은 kind는 안 믿는다
    assert st == 200 and j["created"] and j["record"]["kind"] == "fall" and j["record"]["rev"] == 1 and j["record"]["created_at"]
    st, j = _put(dict(HYBE, ticker="hybe"), None)
    assert st == 400 and "티커" in j["error"]
    st, j = _put(dict(HYBE, id="tl_other"), 5)                         # 없는 id + base_rev → 되살리지 않음
    assert st == 409 and j["code"] == "gone"
    st, j = _put(dict(HYBE, note="수정", created_at="2026-11-19T00:00:00+09:00"), 0)   # 낡은 rev
    assert st == 409 and j["code"] == "conflict"
    st, j = _put(dict(HYBE, note="수정"), 1)
    assert st == 200 and j["record"]["rev"] == 2 and j["record"]["note"] == "수정"
    st, j = _put(dict(HYBE, note="수정", view={"x": 1}), 2)             # 화면이 붙여 보낸 view는 버린다
    assert st == 200 and "view" not in j["record"]


def test_list_view_fields_and_price_failure_visible(store):
    _list(0)
    _put(HYBE, None)
    _put({"id": "tl_x", "ticker": "ZZZZ", "name": "없는 종목", "label": "수평선", "scale": "log",
          "p1": ["2026-10-01", 10], "p2": ["2026-11-01", 10], "status": "추적"}, None)
    j = _list(1)
    by = {x["id"]: x for x in j["lines"]}
    h = by["tl_hybe_top"]["view"]
    exp = tl.line_value(HYBE, "2026-11-19")
    assert abs(h["value"] - exp) < 1e-6 and h["close"] == 189400.0 and h["zone"] == "above"
    assert abs(h["dist_pct"] - (189400 / exp - 1) * 100) < 1e-9
    assert by["tl_x"]["view"]["zone"] == "unknown"
    assert j["n_price_failed"] >= 1                                    # 조용히 빠지지 않는다


def test_bot_read_open_and_writes_need_session():
    assert "/api/track-lines" in app._BOT_READ_EXACT_PATHS
    assert not any(p.startswith("/api/track-lines/") for p in app._BOT_READ_EXACT_PATHS)


def test_page_and_header_link():
    html = open(os.path.join(ROOT, "static", "track.html"), encoding="utf-8").read()
    assert html.count("/api/track-lines") >= 4 and "Coordinates" in html
    idx = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
    assert idx.count('href="/track"') == 1
    strip = idx[idx.index('id="hdrStrip"'):idx.index('id="themeToggle"')]
    assert 'class="hdr-track" href="/track"' in strip and "추적장부" in strip      # v5.389 헤더 띠의 채운 버튼(윗줄은 자리가 없어 탭이 가려진다)
    assert ".hdr-track{" in idx and "background:var(--n-accent)" in idx.split(".hdr-track{")[1].split("}")[0]


# ── v5.390 화면: 종목별 묶기(production 원문을 node로 실행) ──────────────────────────
# 사보타주 확인(2026-11-19, FAIL 확인 후 원복): ① 종료한 선 숨기기 조건 제거 ② 종목 정렬(가까운 순) 제거 ③ 선을 값 내림차순으로 정렬하는 줄 제거
def _extract_js(name: str) -> str:
    src = open(os.path.join(ROOT, "static", "track.html"), encoding="utf-8").read()
    start = src.index(f"function {name}(")
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        depth += {"{": 1, "}": -1}.get(src[j], 0)
        if depth == 0:
            return src[start:j + 1]
    raise AssertionError(name)


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_group_stocks_order_and_filters():
    L = [
        {"id": "a1", "ticker": "AAA", "name": "에이", "theme": "", "status": "추적", "view": {"value": 90, "close": 100, "zone": "below", "to_line_pct": -10, "value_date": "2026-11-19"}},
        {"id": "a2", "ticker": "AAA", "name": "에이", "theme": "", "status": "추적", "view": {"value": 130, "close": 100, "zone": "above", "to_line_pct": 30, "value_date": "2026-11-19"}},
        {"id": "b1", "ticker": "BBB", "name": "비", "theme": "한한령", "status": "추적", "view": {"value": 51, "close": 50, "zone": "near", "to_line_pct": 2, "value_date": "2026-11-19"}},
        {"id": "b2", "ticker": "BBB", "name": "비", "theme": "한한령", "status": "종료", "view": {"value": 70, "close": 50, "zone": "above", "to_line_pct": 40, "value_date": "2026-11-19"}},
        {"id": "c1", "ticker": "CCC", "name": "씨", "theme": "", "status": "추적", "view": {"value": 10, "close": None, "zone": "unknown", "to_line_pct": None, "value_date": "2026-11-19"}},
        {"id": "d1", "ticker": "DDD", "name": "디", "theme": "", "status": "추적", "view": {"value": 96, "close": 100, "zone": "above", "to_line_pct": -4, "value_date": "2026-11-19"}},
    ]
    src = (_extract_js("groupStocks") + f"\nconst L={json.dumps(L, ensure_ascii=False)};"
           + "const pick=G=>G.map(g=>({t:g.ticker,ids:g.lines.map(l=>l.id),near:g.near.length,close:g.close}));"
           + "console.log(JSON.stringify({all:pick(groupStocks(L,'',false)),end:pick(groupStocks(L,'',true)),th:pick(groupStocks(L,'한한령',false))}));")
    out = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)
    assert [g["t"] for g in r["all"]] == ["BBB", "DDD", "AAA", "CCC"]        # 선까지 가까운 순(근처가 맨 앞) → 가격 못 받은 종목은 맨 뒤
    assert r["all"][0]["ids"] == ["b1"] and r["all"][0]["near"] == 1          # 종료한 선은 기본 숨김
    assert [g for g in r["all"] if g["t"] == "AAA"][0]["ids"] == ["a2", "a1"]  # 한 종목 안에서는 높은 선부터(사다리)
    assert [g for g in r["end"] if g["t"] == "BBB"][0]["ids"] == ["b2", "b1"]  # "종료한 선 보기"면 같이
    assert [g["t"] for g in r["th"]] == ["BBB"]
    assert [g for g in r["all"] if g["t"] == "CCC"][0]["close"] is None


def test_page_uses_scanner_theme_and_server_distance():
    html = open(os.path.join(ROOT, "static", "track.html"), encoding="utf-8").read()
    assert "localStorage.getItem('scannerTheme')" in html and ':root[data-theme="dark"]{' in html
    assert "to_line_pct" in html and "value/close" not in html.replace(" ", "")   # 거리는 서버 값을 쓴다(화면에서 다시 계산 안 함)


# ── v5.391 차트 ──────────────────────────────────────────────────────────────
# 사보타주 확인(2026-11-19, 전부 FAIL 확인 후 원복): 이름표 간격 제거 / 먼 선 축 제한 제거 / 주봉 종가를 first로 /
# 첫 점 이전 None 제거 / 엔드포인트에서 종료 선 포함.
def _bars(n, start="2026-01-05"):
    import pandas as pd
    idx = pd.bdate_range(start, periods=n)
    c = [100.0 + i for i in range(n)]
    return pd.DataFrame({"Open": c, "High": [v + 2 for v in c], "Low": [v - 2 for v in c], "Close": c}, index=idx)


def _line(d1, p1, d2, p2, **kw):
    return {"id": kw.pop("id", "L1"), "ticker": "T", "label": "빗각", "scale": "log", "p1": [d1, p1], "p2": [d2, p2], **kw}


def test_chart_payload_daily_values_match_line_value():
    from datetime import date
    df = _bars(120)
    ln = _line("2026-02-02", 100, "2026-03-02", 120)
    p = tl.chart_payload(df, [ln])
    assert p["tf"] == "day" and p["log"] is True
    assert len(p["bars"]) == 120  # 첫 점 45일 전(2025-12-19)보다 뒤라 전부 들어간다
    assert len(p["future"]) == max(8, int(120 * tl.CHART_FUTURE_FRAC))
    vals = p["lines"][0]["values"]
    assert len(vals) == len(p["bars"]) + len(p["future"])
    days = [b[0] for b in p["bars"]] + p["future"]
    for d, v in zip(days, vals):
        if d < "2026-02-02":
            assert v is None, d  # 선을 긋기 전 과거로는 늘리지 않는다
        else:
            assert v == pytest.approx(tl.line_value(ln, date.fromisoformat(d))), d
    assert vals[-1] is not None and p["future"][0] > p["bars"][-1][0]
    # 봉 값은 원본 그대로
    assert p["bars"][0][1:] == [100.0, 102.0, 98.0, 100.0]


def test_chart_payload_starts_pad_days_before_first_point():
    df = _bars(300, start="2025-01-06")
    p = tl.chart_payload(df, [_line("2025-12-01", 100, "2026-01-05", 110)])
    assert p["start"] == "2025-10-17" and p["bars"][0][0] >= "2025-10-17"
    assert p["tf"] == "day"


def test_chart_payload_switches_to_weekly_above_limit():
    df = _bars(tl.CHART_MAX_DAILY + 200, start="2024-01-01")
    ln = _line("2024-03-06", 100, "2025-03-05", 200, scale="linear")
    p = tl.chart_payload(df, [ln])
    assert p["tf"] == "week" and p["log"] is False
    assert len(p["bars"]) < tl.CHART_MAX_DAILY
    # 주봉 = 그 주의 첫 시가·최고 고가·최저 저가·마지막 종가, 날짜는 그 주 마지막 거래일
    w = df[(df.index >= "2024-01-22") & (df.index <= "2024-01-26")]  # 첫 점 45일 전 = 01-21
    assert p["bars"][0] == ["2024-01-26", float(w.Open.iloc[0]), float(w.High.max()), float(w.Low.min()), float(w.Close.iloc[-1])]
    # 첫 점(수요일)이 든 주의 봉(금요일 날짜)부터 값이 있다
    days = [b[0] for b in p["bars"]]
    i = days.index("2024-03-08")
    assert p["lines"][0]["values"][i] is not None and p["lines"][0]["values"][i - 1] is None
    # 미래는 주 단위
    from datetime import date
    assert (date.fromisoformat(p["future"][1]) - date.fromisoformat(p["future"][0])).days == 7


def test_chart_payload_empty_window():
    p = tl.chart_payload(_bars(10, start="2020-01-06"), [_line("2026-02-02", 100, "2026-03-02", 120)])
    assert p["bars"] == [] and p["lines"] == []


def test_chart_endpoint(store, monkeypatch):
    _put({"id": "c1", "ticker": "005930.KS", "name": "삼성전자", "label": "빗각", "scale": "log",
          "p1": ["2026-02-02", 100], "p2": ["2026-03-02", 120], "status": "추적"}, None)
    _put({"id": "c2", "ticker": "005930.KS", "name": "삼성전자", "label": "수평선", "scale": "log",
          "p1": ["2026-02-02", 90], "p2": ["2026-03-02", 90], "status": "종료"}, None)
    seen = {}

    def fake(t, start):
        seen["t"], seen["start"] = t, start
        return _bars(120)
    monkeypatch.setattr(app, "_track_bars", fake)
    r = asyncio.run(app.track_lines_chart("005930.ks"))
    j = json.loads(r.body)
    assert r.status_code == 200 and j["ok"] and j["ticker"] == "005930.KS"
    assert [x["id"] for x in j["lines"]] == ["c1"]  # 종료한 선은 안 그린다
    assert str(seen["start"]) == "2025-12-19"
    # 일봉 실패는 조용한 빈 차트가 아니라 502
    monkeypatch.setattr(app, "_track_bars", lambda t, s: None)
    assert asyncio.run(app.track_lines_chart("005930.KS")).status_code == 502
    assert asyncio.run(app.track_lines_chart("NOPE")).status_code == 404
    # 봇 토큰으로는 안 열린다(세션 전용)
    assert not any("chart" in p for p in app._BOT_READ_EXACT_PATHS)


def test_chart_svg_draws_candles_and_lines():
    if not shutil.which("node"):
        pytest.skip("node 없음")
    p = tl.chart_payload(_bars(60), [
        _line("2026-02-02", 100, "2026-03-02", 120, id="a"),
        _line("2026-02-02", 131, "2026-03-02", 131, id="b", label="수평선"),
        _line("2026-02-02", 132, "2026-03-02", 132, id="c", label="채널 상단"),
        _line("2026-02-02", 9000, "2026-03-02", 9000, id="far", label="먼 선")])
    src = open(os.path.join(os.path.dirname(__file__), "static", "track.html"), encoding="utf-8").read()
    colors = [l for l in src.splitlines() if l.startswith("const LINE_COLORS")][0]
    js = colors + "\n" + _extract_js("chartSVG") + "\nconst p=" + json.dumps(p) + """;
const s=chartSVG(p,0);const cnt=re=>(s.match(re)||[]).length;
const ys=[...s.matchAll(/<text class="clb"[^>]* y="([\\d.]+)"[^>]*>([^<]+)</g)].map(m=>[parseFloat(m[1]),m[2]]);
console.log(JSON.stringify({rect:cnt(/<rect class="c[ud]"/g),solid:cnt(/<path class="cl" /g),fut:cnt(/<path class="cl fut"/g),ys,nan:/NaN|undefined|Infinity/.test(s)}));"""
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert out["rect"] == 60 and out["nan"] is False
    # 봉 범위 밖 멀리 있는 선(9000)은 축을 늘리지 않고 잘린다 → 그려지는 선 3개
    assert out["solid"] == 3 and out["fut"] == 3
    labels = out["ys"]
    assert len(labels) == 3 and not any("먼 선" in t for _, t in labels)
    ys = [y for y, _ in labels]
    assert ys == sorted(ys) and all(b - a >= 13 - 1e-6 for a, b in zip(ys, ys[1:]))  # 안 겹치고 순서 유지
    # 위에 있는 선(가격 큰 쪽)이 위 이름표
    px = [float(t.split()[-1].replace(",", "")) for _, t in labels]
    assert px == sorted(px, reverse=True) and {131.0, 132.0} <= set(px)
