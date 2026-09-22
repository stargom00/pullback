"""v5.278 — 🔺 ABC ★ 등록이 **일지에 실제로 들어가는가**.

[증상] ★ → `/api/watch/quick` → **409**, 그런데 일지엔 아무것도 없음.

[원인] v5.106 안전장치 `reg_price >= pivot → 409(already_above_pivot)`.
abcWatch가 `pivot=MA200`, `reg_price=현재가`를 보내는데 **C2·C3는 정의상
close > MA200**이라 ★을 누를 만한 종목이 100% 409였다. 프론트는 409를 받으면
`openPivotChoiceModal(s,...)`을 띄우지만 그 모달은 **스캔 히트 모양**을
기대해서 ABC 히트로는 제대로 안 뜨고, **레코드가 하나도 안 생겼다**.

[수정] `myTrackSaveAdd()`와 같은 형태 — `my_trigger_price` + `setJournal`.
pivot 비교가 없으니 409 경로 자체를 안 탄다.

[트리거] 항상 MA200(벽), 방향만 가격 위치로:
  벽 아래(C0·C1) → above  /  벽 위(C2·C3) → below(눌림 재터치)
사용자 초안의 "C2·C3는 트리거 없이 관찰만"은 `my_trigger_price=null`인 pending이
**14일 뒤 자동 무산**되는 문제가 있어(WATCH_DAYS) 방향을 뒤집는 쪽을 택했다.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
TEXT = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    for decl in (f"async function {name}(", f"function {name}("):
        i = TEXT.find(decl)
        if i != -1:
            break
    assert i != -1, name
    b = TEXT.index("{", i)
    d = 0
    for k in range(b, len(TEXT)):
        if TEXT[k] == "{":
            d += 1
        elif TEXT[k] == "}":
            d -= 1
            if d == 0:
                return TEXT[i:k + 1]
    raise AssertionError(name)


SRC = _fn("abcWatch")


def _register(hit, journal=None):
    """production abcWatch를 **그대로** 실행해 일지에 뭐가 들어가는지 본다."""
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    harness = f"""
let _journal = {json.dumps(journal or [], ensure_ascii=False)};
let _abcData = {{hits: [{json.dumps(hit, ensure_ascii=False)}]}};
let _saved = null, _fetched = [];
function getJournal() {{ return _journal; }}
async function setJournal(j) {{ _saved = j; }}
function kstStr() {{ return '2026-09-22'; }}
function _abcStageLabel() {{ return 'MA200'; }}
// 이 둘 중 하나라도 불리면 옛 경로로 되돌아간 것이다
async function _quickWatchRequest() {{ _fetched.push('quickWatch'); }}
async function fetch(u) {{ _fetched.push(String(u)); return {{json: async () => ({{}})}}; }}
const btn = {{}};
{SRC}
abcWatch(btn, {json.dumps(hit['ticker'])}).then(() => {{
  console.log(JSON.stringify({{saved: _saved, btn: btn.textContent, net: _fetched}}));
}});
"""
    p = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip())


def hit(stage, close, ma=1000.0, **kw):
    d = {"ticker": "123456.KQ", "name": "테스트", "c_stage": stage,
         "close": close, "ma_stage": ma, "stage_pct": round((close / ma - 1) * 100, 1),
         "sector": "반도체", "gate_break": None}
    d.update(kw)
    return d


# ── 네 단계 전부 등록되는가 ─────────────────────────────────────────
@pytest.mark.parametrize("stage,close", [
    ("C0 대기", 900.0),      # 벽 한참 아래
    ("C1 벽앞", 980.0),      # 벽 바로 아래
    ("C2 진돌이", 1080.0),   # 벽 위 — 옛 경로에선 409였다
    ("C3 이탈", 1300.0),     # 벽 한참 위 — 옛 경로에선 409였다
])
def test_every_stage_registers(stage, close):
    r = _register(hit(stage, close))
    assert r["saved"] is not None, f"{stage}: 일지에 저장이 안 됐다"
    assert len(r["saved"]) == 1, r["saved"]
    assert r["btn"] == "✓ 추적중"


def test_no_network_call_at_all():
    """`/api/watch/quick`을 안 탄다 — 409의 출처였다."""
    for stage, close in (("C2 진돌이", 1080.0), ("C0 대기", 900.0)):
        r = _register(hit(stage, close))
        assert r["net"] == [], f"{stage}: 네트워크를 탔다 — {r['net']}"


# ── 트리거 방향 ─────────────────────────────────────────────────────
def test_trigger_is_always_the_wall():
    for stage, close in (("C0 대기", 900.0), ("C3 이탈", 1300.0)):
        rec = _register(hit(stage, close))["saved"][0]
        assert rec["my_trigger_price"] == 1000.0, (stage, rec["my_trigger_price"])


def test_direction_follows_which_side_of_the_wall():
    below = _register(hit("C1 벽앞", 980.0))["saved"][0]
    above = _register(hit("C2 진돌이", 1080.0))["saved"][0]
    assert below["my_trigger_dir"] == "above", "벽 아래인데 아래로 기다린다"
    assert above["my_trigger_dir"] == "below", "벽 위인데 위로 기다린다"


def test_trigger_is_never_null():
    """null이면 **14일 뒤 자동 무산**된다(WATCH_DAYS) — 관찰이 조용히 사라진다."""
    for stage, close in (("C0 대기", 900.0), ("C1 벽앞", 980.0),
                         ("C2 진돌이", 1080.0), ("C3 이탈", 1300.0)):
        rec = _register(hit(stage, close))["saved"][0]
        assert rec["my_trigger_price"] is not None, stage


def test_watch_days_expiry_still_targets_null_triggers_only():
    """위 테스트의 전제 — 만료 조건이 바뀌면 같이 봐야 한다."""
    i = TEXT.index("waitDays >= WATCH_DAYS")
    assert "r.my_trigger_price == null" in TEXT[i:i + 120], TEXT[i:i + 120]


# ── 일지 레코드 모양 ────────────────────────────────────────────────
def test_record_marks_its_origin_and_stays_out_of_auto_judgement():
    rec = _register(hit("C2 진돌이", 1080.0))["saved"][0]
    assert rec["tab"] == "ABC", "출처가 안 남는다"
    assert rec["manual"] is True, "자동 판정에 섞인다"
    assert rec["category"] == "재량" and rec["status"] == "pending"
    assert rec["pivot"] is None and rec["entry"] is None and rec["stop"] is None, rec


def test_note_records_the_stage_at_registration():
    rec = _register(hit("C2 진돌이", 1080.0, gate_break={"bars_ago": 2}))["saved"][0]
    assert "C2 진돌이" in rec["note"] and "MA200" in rec["note"], rec["note"]
    assert "600돌파 D+2" in rec["note"], rec["note"]


def test_duplicate_is_refused_without_touching_the_journal():
    existing = [{"ticker": "123456.KQ", "tab": "ABC", "status": "pending"}]
    r = _register(hit("C2 진돌이", 1080.0), journal=existing)
    assert r["saved"] is None, "중복인데 또 썼다"
    assert r["btn"] == "✓ 추적중"


def test_closed_record_does_not_block_re_registration():
    for st in ("closed", "missed", "archived"):
        old = [{"ticker": "123456.KQ", "tab": "ABC", "status": st}]
        r = _register(hit("C2 진돌이", 1080.0), journal=old)
        assert r["saved"] is not None, f"{st}인데 재등록이 막혔다"


def test_missing_wall_is_reported_not_saved():
    r = _register(hit("C2 진돌이", 1080.0, ma_stage=None))
    assert r["saved"] is None
    assert "없음" in (r["btn"] or ""), r["btn"]
