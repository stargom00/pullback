"""v5.280 — 일지 UX 3건(사용자 지시).

1. **종목코드 칸에 한글 종목명** → 코드 자동 해석. 프로덕션 리졸버
   (`universe.resolve_name_to_ticker`)를 `/api/lookup`으로 재사용한다 —
   프론트에 두 번째 매칭 규칙을 만들지 않는다. 시장은 **코드에서** 판정하므로
   종목명만 보고 US로 오판하던 문제도 같이 사라진다.
2. **목록 순서** — 보유가 스크롤 없이 보이도록 통계 카드를 목록 아래로.
3. **게이트가 기록을 막지 않는다** — 빨강이어도 진입 저장을 허용하고
   "역행" 태그만 남긴다. 예전엔 강제로 대기로 돌려 **이미 산 종목의 진입
   기록이 불가**했고 실전 R이 유실됐다(기록과 억제를 섞은 것).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

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


def _code(src: str) -> str:
    """`//` 주석 제거 — 변경 이력 주석에 걸려 오탐하는 걸 막는다."""
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("//"):
            continue
        out.append(line.split("//")[0] if "//" in line and "://" not in line else line)
    return "\n".join(out)


# ── 1. 한글 종목명 해석 ─────────────────────────────────────────────
def test_korean_name_triggers_lookup():
    src = _code(_fn("onMaTickerOrTabChange"))
    assert "/[가-힣]/.test(ticker)" in src, "한글 판정이 없다"
    assert "_maResolveName(" in src, "해석을 호출하지 않는다"


def test_lookup_reuses_the_production_resolver():
    """프론트에 **두 번째 매칭 규칙**을 만들면 서버와 갈라진다."""
    src = _code(_fn("_maResolveName"))
    assert "'/api/lookup/' + encodeURIComponent" in src, src
    for bad in ("includes(", "startsWith(", "indexOf("):
        assert bad not in src, f"프론트가 직접 이름 매칭을 한다: {bad}"


def test_market_comes_from_the_code_not_the_name():
    """이름으로 US 오판하던 버그의 수정점 — 시장은 **코드**가 정한다."""
    src = _code(_fn("_maApplyTicker"))
    assert "_maInferMarket(ticker)" in src, src
    infer = _code(_fn("_maInferMarket"))
    assert "KS|KQ" in infer and "_isKrCodeBody" in infer


def test_multiple_candidates_are_offered_not_guessed():
    """후보가 여러 개면 고르게 한다 — 멋대로 하나를 집으면 조용히 틀린다."""
    src = _code(_fn("_maResolveName"))
    assert "d.candidates" in src and "_maShowNameCandidates(" in src, src


def test_lookup_failure_is_visible():
    src = _code(_fn("_maResolveName"))
    assert "못 찾음" in src, "실패가 조용히 넘어간다"
    assert "조회 실패" in src, "예외가 조용히 넘어간다"


# ── 2. 목록이 통계보다 위 ───────────────────────────────────────────
def test_list_comes_before_the_stat_cards():
    """"내가 지금 뭘 들고 있나"가 스크롤 없이 보여야 한다(사용자 지시)."""
    src = _fn("renderJournal")
    assert src.index('<table class="jtable">') < src.index("jrnl-stats"), \
        "통계 카드가 아직 목록보다 위에 있다"


def test_heavy_charts_are_below_the_list_too():
    src = _fn("renderJournal")
    t = src.index('<table class="jtable">')
    for fn in ("renderJCal(", "renderRCurve(", "renderSignalValidation(",
               "renderPaperTrackCard("):
        assert src.index(fn) > t, f"{fn}가 목록보다 위에 있다"


def test_default_tab_is_holdings():
    m = re.search(r"let journalTab = '(\w+)'", TEXT)
    assert m and m.group(1) == "entered", m and m.group(1)


# ── 3. 게이트는 기록을 막지 않는다 ──────────────────────────────────
def test_gate_no_longer_forces_pending():
    """예전 문구 "진입 대신 '관찰(대기)'로 저장할까요?"는 강제 전환이었다."""
    src = _code(_fn("saveJournal"))
    assert "진입 대신" not in src, "강제 대기 전환이 남아 있다"
    assert "forcePending = true" not in src, "여전히 pending으로 돌린다"


def test_gate_defiance_is_recorded():
    """억제 대신 **기록** — 나중에 순응/역행 EV 비교의 데이터가 된다."""
    src = _code(_fn("saveJournal"))
    assert "gateDefiance = gate.reason" in src, src
    assert "gate_defiance: gateDefiance" in src, "레코드에 안 남는다"


def test_mark_entered_no_longer_returns_early_on_gate():
    """대기→진입 전환도 막혀 있었다 — 이미 산 종목을 진입으로 못 바꿨다."""
    src = _code(_fn("markEntered"))
    i = src.index("entryGate()")
    after = src[i:i + 400]
    assert "대기 상태를 유지합니다" not in after, "아직 전환을 막는다"
    assert "r.gate_defiance" in after, "역행 태그를 안 남긴다"


def test_warning_text_is_kept():
    """막지 않는다고 경고까지 없애면 안 된다(사용자 지시: 경고는 유지)."""
    src = _code(_fn("saveJournal"))
    assert "gate.reason" in src, "경고 사유를 안 보여준다"
    assert "권장하지 않지만" in src, "경고 문구가 사라졌다"


def test_dashboard_gate_call_is_untouched():
    """대시보드 표시용 `entryGate()`는 차단이 아니다 — 건드리면 안 된다."""
    src = _fn("renderJournal")
    assert "const _gate = entryGate();" in src
