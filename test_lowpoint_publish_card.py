"""v5.293 저점종목 홈 카드 — 게시 파일 병합 · 낡음 판정 · 카드 3상태.

세 층을 각각 실제 코드로 검증한다(재구현 금지):
① scripts/screens/lowpoint.py의 게시(merge_publish/write_publish) — week 실행이
   month 결과를 지우지 않는가
② app.py의 낡음 판정(_lowpoint_expected_label/_lowpoint_view) — 파일이 없을 때
   None, 오래된 기준봉이면 stale=True
③ static/index.html의 lowpointSectionStatus — 미실행/0건/N건 3상태 구분
   (CLAUDE.md "텍스트 추출 + Node 실행" 레시피 — 프론트 함수를 그대로 돌린다)

사보타주 확인(2026-09-28, 전부 FAIL 확인 후 원복):
① write_publish가 merge 대신 `{tf: entry}`만 쓰도록 → test_week_publish_keeps_month FAIL
② _lowpoint_view가 stale 계산을 지우고 항상 False → test_stale_when_bar_date_old FAIL
③ lowpointSectionStatus가 missing과 empty를 같은 문구로 → test_card_three_states FAIL
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "scripts" / "screens"))
sys.path.insert(0, str(_ROOT))

import lowpoint as lp  # noqa: E402

IDX = _ROOT / "static" / "index.html"


def _app_module():
    """app.py 전체 import는 무겁고 부작용이 있어(스케줄러·캐시) 쓰지 않는다 —
    저점종목 헬퍼만 텍스트로 떼어내 독립 모듈로 실행한다. 사본이 아니라
    app.py의 그 코드 자체다(파일에서 잘라 그대로 exec)."""
    src = (_ROOT / "app.py").read_text(encoding="utf-8")
    start = src.index("LOWPOINT_LATEST_PATH = ")
    end = src.index('@app.get("/api/calendar")')
    body = src[start:end]
    ns = {
        "os": os, "datetime": datetime, "timedelta": timedelta, "timezone": timezone,
        "_json": json, "KST": timezone(timedelta(hours=9)),
        "__file__": str(_ROOT / "app.py"),
        "KR_CLOSE_CONFIRMED_HM": 20 * 60 + 10,
    }
    exec(compile(body, "app.py:lowpoint", "exec"), ns)
    return ns


APP = _app_module()
KST = timezone(timedelta(hours=9))


# ── ① 게시 파일 병합 ──────────────────────────────────────────────────

def _entry(bar_date, n=1):
    return {"bar_date": bar_date, "run_stamp": {"run_at_kst": f"{bar_date} 21:00 KST"},
            "markets": ["KOSPI"], "excluded_counts": {},
            "rows": [{"market": "KOSPI", "code": "005930.KS", "name": "삼성전자",
                      "bar_date": bar_date, "close0": 71200, "close1": 70800,
                      "rsi2": 31.2, "rsi1": 28.4, "rsi0": 29.9}] * n}


def test_week_publish_keeps_month(tmp_path):
    path = str(tmp_path / "lowpoint_latest.json")
    lp.write_publish("month", _entry("2026-08-31"), path)
    lp.write_publish("week", _entry("2026-09-25"), path)
    got = json.loads(Path(path).read_text(encoding="utf-8"))
    assert set(got) == {"week", "month"}, "week 실행이 month 칸을 지웠다"
    assert got["month"]["bar_date"] == "2026-08-31"
    assert got["week"]["bar_date"] == "2026-09-25"


def test_publish_overwrites_same_tf_only(tmp_path):
    path = str(tmp_path / "lowpoint_latest.json")
    lp.write_publish("week", _entry("2026-09-18", n=2), path)
    lp.write_publish("week", _entry("2026-09-25"), path)
    got = json.loads(Path(path).read_text(encoding="utf-8"))
    assert got["week"]["bar_date"] == "2026-09-25" and len(got["week"]["rows"]) == 1


def test_merge_publish_is_pure():
    existing = {"month": _entry("2026-08-31")}
    out = lp.merge_publish(existing, "week", _entry("2026-09-25"))
    assert "week" in out and "month" in out
    assert "week" not in existing, "입력 dict를 제자리에서 고쳤다"


def test_publish_corrupt_file_fails_loud(tmp_path):
    path = tmp_path / "lowpoint_latest.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError):
        lp.write_publish("week", _entry("2026-09-25"), str(path))
    assert path.read_text(encoding="utf-8") == "{not json", "깨진 파일을 조용히 덮어썼다"


def test_publish_entry_shape_from_screen_rows():
    """screen_market()이 만드는 행 구조(한글 키)를 그대로 받아 게시 스키마로
    바꾸는지 — 열 이름이 바뀌면 여기서 깨진다."""
    res = {"market": "kospi", "universe": 800, "fetched": 790, "failed": ["1.KS"],
           "stale": {"2.KS": "2026-01-02"}, "short": {"3.KS": 10}, "meta": {},
           "rows": [dict(zip(lp.COLS, ["KOSPI", "005930.KS", "삼성전자", "2026-09-25",
                                       71200, 70800, 31.2, 28.4, 29.9, None]))]}
    import pandas as pd
    entry = lp.publish_entry([res], "week", {"kospi": pd.Timestamp("2026-09-25")},
                             {"run_at_kst": "x"})
    assert entry["bar_date"] == "2026-09-25"
    assert entry["rows"] == [{"market": "KOSPI", "code": "005930.KS", "name": "삼성전자",
                              "bar_date": "2026-09-25", "close0": 71200, "close1": 70800,
                              "rsi2": 31.2, "rsi1": 28.4, "rsi0": 29.9, "price_note": None}]
    assert entry["excluded_counts"]["KOSPI"] == {"universe": 800, "fetched": 790,
                                                 "failed": 1, "stale": 1, "short": 1,
                                                 "admin_excluded": 0, "seam": {}}


# ── ② 서버 낡음 판정 ─────────────────────────────────────────────────

@pytest.mark.parametrize("now,week,month", [
    # 금요일 20:10 KST는 아직 그 주 라벨의 마감 기준(라벨 다음날 20:10) 전
    ("2026-09-25 23:00", "2026-09-18", "2026-08-31"),
    ("2026-09-26 20:09", "2026-09-18", "2026-08-31"),
    ("2026-09-26 20:11", "2026-09-25", "2026-08-31"),
    ("2026-09-28 10:00", "2026-09-25", "2026-08-31"),
    ("2026-10-01 20:11", "2026-09-25", "2026-09-30"),   # 9월 말일 다음날 20:10 이후
    ("2026-10-01 20:09", "2026-09-25", "2026-08-31"),
])
def test_expected_label(now, week, month):
    n = datetime.fromisoformat(now).replace(tzinfo=KST)
    assert APP["_lowpoint_expected_label"]("week", n) == week
    assert APP["_lowpoint_expected_label"]("month", n) == month


def test_week_label_saturday_belongs_to_next_friday():
    # pandas W-FRI 구간 정의와 같아야 한다(lowpoint.resample_bars) — 토요일은
    # 다음 금요일 구간. 어긋나면 낡음 판정이 한 주씩 밀린다.
    import pandas as pd
    for d in pd.date_range("2026-09-21", "2026-10-05", freq="D"):
        got = APP["_lowpoint_period_label"]("week", d.date())
        want = pd.Series([1.0], index=[d]).resample("W-FRI").last().index[0].date()
        assert got == want, d


def test_month_label_matches_pandas_me():
    import pandas as pd
    for d in pd.date_range("2026-01-01", "2026-12-31", freq="7D"):
        got = APP["_lowpoint_period_label"]("month", d.date())
        want = pd.Series([1.0], index=[d]).resample("ME").last().index[0].date()
        assert got == want, d


def _view(tmp_path, payload, now="2026-09-28 10:00"):
    path = tmp_path / "lowpoint_latest.json"
    if payload is not None:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    APP["LOWPOINT_LATEST_PATH"] = str(path)
    return APP["_lowpoint_view"](datetime.fromisoformat(now).replace(tzinfo=KST))


def test_view_missing_file_is_none(tmp_path):
    assert _view(tmp_path, None) is None


def test_view_corrupt_file_is_none(tmp_path, capsys):
    path = tmp_path / "lowpoint_latest.json"
    path.write_text("[1,2]", encoding="utf-8")   # dict가 아님
    APP["LOWPOINT_LATEST_PATH"] = str(path)
    assert APP["_lowpoint_view"](datetime.now(KST)) is None
    assert "[lowpoint]" in capsys.readouterr().out, "경고 로그 없이 조용히 None"


def test_view_fresh_not_stale(tmp_path):
    got = _view(tmp_path, {"week": _entry("2026-09-25")})
    assert got["week"]["stale"] is False
    assert got["week"]["expected_bar_date"] == "2026-09-25"
    assert got["month"] is None, "안 돌린 월봉 칸은 None(프론트가 미실행 표시)"
    assert got["expected"] == {"week": "2026-09-25", "month": "2026-08-31"}


def test_stale_when_bar_date_old(tmp_path):
    got = _view(tmp_path, {"week": _entry("2026-09-18"), "month": _entry("2026-08-31")})
    assert got["week"]["stale"] is True, "지난 주 기준봉인데 낡음 판정이 안 됐다"
    assert got["month"]["stale"] is False


def test_stale_when_bar_date_missing(tmp_path):
    e = _entry("2026-09-25")
    e.pop("bar_date")
    assert _view(tmp_path, {"week": e})["week"]["stale"] is True


def test_view_keeps_rows_untouched(tmp_path):
    e = _entry("2026-09-25")
    got = _view(tmp_path, {"week": e})
    assert got["week"]["rows"] == e["rows"]


def test_calendar_payload_includes_lowpoint():
    src = (_ROOT / "app.py").read_text(encoding="utf-8")
    assert '"lowpoint": lowpoint,' in src
    assert src.count("_lowpoint_view(") >= 2, "정의만 있고 호출부가 없다"


# ── ③ 프론트 카드 3상태(node로 production 함수 실행) ─────────────────

def _extract(name: str) -> str:
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


def _meta_const() -> str:
    src = IDX.read_text(encoding="utf-8")
    start = src.index("const _LOWPOINT_TF_META = {")
    end = src.index("};", start) + 2
    return src[start:end]


def _run_js(snippet: str):
    src = _meta_const() + "\n" + _extract("lowpointSectionStatus") + "\n" + snippet
    out = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node 미설치")


@needs_node
def test_card_three_states():
    got = _run_js(
        "const e = {bar_date:'2026-09-25', rows:[{code:'005930.KS'}], stale:false};"
        "const empty = {bar_date:'2026-09-25', rows:[], stale:false};"
        "console.log(JSON.stringify({missing: lowpointSectionStatus(null,'week'),"
        " empty: lowpointSectionStatus(empty,'week'), rows: lowpointSectionStatus(e,'week'),"
        " missingMonth: lowpointSectionStatus(null,'month'),"
        " emptyMonth: lowpointSectionStatus(empty,'month')}));")
    assert got["missing"]["state"] == "missing" and got["missing"]["text"] == "이번 주 미실행"
    assert got["empty"]["state"] == "empty" and got["empty"]["text"] == "이번 주 신호 없음"
    assert got["rows"]["state"] == "rows" and got["rows"]["count"] == 1
    assert got["rows"]["text"] == ""
    # 미실행(안 돌렸다)과 0건(돌렸고 신호 없음)은 문구가 달라야 한다
    assert got["missing"]["text"] != got["empty"]["text"]
    assert got["missingMonth"]["text"] == "이번 달 미실행"
    assert got["emptyMonth"]["text"] == "이번 달 신호 없음"


@needs_node
def test_card_stale_badge_text():
    got = _run_js(
        "const s = {bar_date:'2026-09-18', rows:[], stale:true};"
        "console.log(JSON.stringify({w: lowpointSectionStatus(s,'week'),"
        " m: lowpointSectionStatus(s,'month'),"
        " fresh: lowpointSectionStatus({bar_date:'x',rows:[],stale:false},'week')}));")
    assert got["w"]["stale"] is True and got["w"]["staleText"] == "이번 주 미실행(기준봉 낡음)"
    assert got["m"]["staleText"] == "이번 달 미실행(기준봉 낡음)"
    assert got["fresh"]["staleText"] == ""


@needs_node
def test_card_renders_without_json():
    """lp=null이어도 카드 자체는 사라지지 않는다(숨기면 안 돌린 걸 모른다)."""
    src = (_meta_const() + "\n" + _extract("lowpointSectionStatus") + "\n"
           + _extract("lpDisplayName") + "\n"
           + _extract("_lowpointRowHtml") + "\n" + _extract("_lowpointSectionHtml") + "\n"
           + _extract("renderLowpointHtml") + "\n"
           + "function tvUrl(t, m, iv) { return `tv:${t}:${m}:${iv}`; }\n"
           + "const html = renderLowpointHtml(null);"
           + "console.log(JSON.stringify({html}));")
    out = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    html = json.loads(out.stdout)["html"]
    assert "저점종목" in html and "이번 주 미실행" in html and "이번 달 미실행" in html


@needs_node
def test_card_rows_link_interval_per_section():
    src = (_meta_const() + "\n" + _extract("lowpointSectionStatus") + "\n"
           + _extract("lpDisplayName") + "\n"
           + _extract("_lowpointRowHtml") + "\n" + _extract("_lowpointSectionHtml") + "\n"
           + _extract("renderLowpointHtml") + "\n"
           + "function tvUrl(t, m, iv) { return `tv|${t}|${m}|${iv}`; }\n"
           + "const row = {market:'KOSDAQ', code:'053260.KQ', name:'금강철강',"
           + " close0:1234, close1:1200, rsi2:31.2, rsi1:28.4, rsi0:29.9};"
           + "const us = Object.assign({}, row, {market:'US', code:'AAPL'});"
           + "const html = renderLowpointHtml({week:{bar_date:'2026-09-25',rows:[row],stale:false},"
           + " month:{bar_date:'2026-08-31',rows:[us],stale:false}, expected:{week:'2026-09-25',month:'2026-08-31'}});"
           + "console.log(JSON.stringify({html}));")
    out = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    html = json.loads(out.stdout)["html"]
    assert "tv|053260.KQ|KR|1W" in html, "주봉 섹션 링크가 1W가 아니다"
    assert "tv|AAPL|US|1M" in html, "월봉 섹션 링크가 1M이 아니다"
    assert "31.2→28.4→29.9" in html and "금강철강" in html
    assert "1W" not in html.split("tv|AAPL")[1], "월봉 행에 주봉 interval이 섞였다"


@needs_node
def test_tv_interval_optional_keeps_old_url():
    """interval을 안 넘기면 기존 URL과 완전히 같아야 한다(기존 링크 회귀 방지)."""
    src = ([l for l in IDX.read_text(encoding="utf-8").splitlines()
            if l.strip().startswith("const TV_LAYOUT_ID")][0] + "\n"
           + _extract("tvSymbolUrl") + "\n" + _extract("tvUrl") + "\n"
           + "console.log(JSON.stringify({plain: tvUrl('005930.KS'),"
           + " wk: tvUrl('005930.KS', 'KR', '1W'), us: tvUrl('AAPL', 'US', '1M')}));")
    out = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["plain"].endswith("?symbol=KRX%3A005930") and "interval" not in got["plain"]
    assert got["wk"] == got["plain"] + "&interval=1W"
    assert got["us"].endswith("?symbol=AAPL&interval=1M")
