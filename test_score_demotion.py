"""v5.261 — score 표시 강등이 실제로 반영됐는가.

근거: 2026-09-14 측정 B/B-2에서 score 구성 항목 중 **노출된 17개 전부**가
20일 수익률을 가르지 못했다(docs/pullback_quality_axes*.md).

**계산·정렬 로직 자체는 삭제하지 않는다**(사용자 지시 — 근거가 뒤집힐 수 있음).
바뀐 것은 ① 표시 톤 ② 기본 정렬 2차 키뿐이며, 이 테스트는 그 둘만 고정한다.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
IDX = ROOT / "static" / "index.html"


def test_score_ring_has_no_good_bad_color():
    """초록/노랑으로 좋고 나쁨을 말하던 분기가 없어야 한다."""
    src = IDX.read_text(encoding="utf-8")
    i = src.index("function scoreRing(")
    body = src[i:src.index("\n}", i)]
    assert "var(--green)" not in body and "var(--amber)" not in body, body
    assert "var(--muted)" in body


def test_score_ring_shows_reference_label_and_tooltip():
    src = IDX.read_text(encoding="utf-8")
    i = src.index("function scoreRing(")
    body = src[i:src.index("\n}", i)]
    assert "score-ring-muted" in body
    assert "score-ref" in body and ">참고<" in body
    assert "SCORE_NOTE" in body, "툴팁이 측정 근거를 안 단다"


def test_score_note_text_matches_the_measurement():
    src = IDX.read_text(encoding="utf-8")
    line = [l for l in src.splitlines() if l.strip().startswith("const SCORE_NOTE")][0]
    assert "17항목" in line and "20일 수익률과 무관" in line and "2026-09-14" in line, line


def test_muted_styles_exist():
    src = IDX.read_text(encoding="utf-8")
    assert ".score-ring-muted .score-num{" in src
    assert ".score-ref{" in src
    assert ".cc-score-muted{" in src


def test_collapsed_card_score_is_muted_too():
    src = IDX.read_text(encoding="utf-8")
    i = src.index('class="cc-score')
    span = src[i:src.index("</span>", i)]
    assert "cc-score-muted" in span and "SCORE_NOTE" in span, span


def test_default_sort_no_longer_uses_score():
    """기본 정렬 2차 키가 score면 '위에 있는 게 더 좋다'는 잘못된 신호가 된다."""
    import app
    src = Path(app.__file__).read_text(encoding="utf-8")
    i = src.index("hits.sort(key=lambda x: (x.get(\"triggered\", False)")
    call = src[i:src.index("reverse=True", i)]
    assert "setup_score" not in call and 'x["score"]' not in call, call
    assert 'x.get("rs")' in call, call


def test_score_is_still_computed_and_exposed():
    """강등이지 제거가 아니다 — 필드와 계산은 남아 있어야 한다."""
    import scanner
    src = Path(scanner.__file__).read_text(encoding="utf-8")
    assert '"score": round(score, 1)' in src
    idx = IDX.read_text(encoding="utf-8")
    # 호출부가 2곳(일반 카드·숏 카드)이다. `in` 검사만 하면 한 곳을 지워도
    # 통과한다(사보타주에서 실제로 통과했다) — 개수로 고정한다.
    n = idx.count("scoreRing(s.score)")
    assert n >= 2, f"score 표시 호출부가 줄었다({n}곳) — 강등이지 제거가 아니다"
    assert idx.count('class="cc-score') >= 1


@pytest.mark.skipif(shutil.which("node") is None, reason="node 미설치")
def test_score_ring_renders_without_color_classes():
    """production 함수를 실제로 실행해 출력에 색이 안 들어가는지 확인."""
    src = IDX.read_text(encoding="utf-8")
    i = src.index("const SCORE_NOTE")
    snippet = src[i:src.index("\n}", src.index("function scoreRing(")) + 2]
    out = subprocess.run(
        ["node", "-e", snippet + "\nconsole.log(scoreRing(92) + '\\n---\\n' + scoreRing(30));"],
        capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    html = out.stdout
    assert "--green" not in html and "--amber" not in html, html
    assert html.count("참고") == 2
    # 높은 점수와 낮은 점수의 색이 같아야 한다(등급처럼 보이면 안 됨)
    high, low = html.split("---")
    assert high.count("var(--muted)") == low.count("var(--muted)")
