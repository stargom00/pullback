"""테스트 공용 헬퍼 (v5.291, 사용자 지시).

## 왜 있나

"이 문자열이 소스에 **없어야 한다**"는 검사가 **변경을 설명하는 주석**에 걸려
오탐하는 사고가 2026-09-27 한 세션에서만 **다섯 번** 났다:
  · app.py changelog의 `CONFIRM_RULE_BY_TAB.get(tab)` 인용 (2건)
  · `_abcChip` 주석의 `.journal-btn`
  · `toggleAbcGateBreak` 제거를 설명하는 주석
  · `ma_stage` → `ma_gate` 교체를 설명하는 주석
매번 각 테스트 파일에 같은 헬퍼를 복사해 넣다가 6개 파일에 중복됐다.
CLAUDE.md의 사보타주 패턴 1("'없어야 한다' 검사는 주석·changelog 인용문에 걸려
오탐한다 → 검사 범위를 실행 코드 영역으로 좁힐 것")을 한 곳에서 구현한다.

## 동작 불변을 위한 파라미터

중복돼 있던 6개 구현은 **두 가지 변종**이었고, 합치면서 동작이 바뀌면 안 된다
(사용자 지시 "동작 불변"):
  · 단순형 4곳 — `//`로 **시작하는 줄**만 버린다
  · 강한형 2곳 — 그에 더해 코드 줄의 **후행 `//` 주석**까지 잘라낸다
    (`://`는 URL이라 예외)
그리고 파이썬 소스를 검사하는 2곳은 `#` 줄도 버렸다. 그래서 호출부마다
`comment_markers`·`strip_trailing`을 명시해 기존 동작을 1:1로 유지한다 —
기본값을 "가장 강한 동작"으로 두면 조용히 검사 범위가 넓어진다.
"""
from __future__ import annotations


def code_only(src: str, *, comment_markers: tuple = ("//",),
              strip_trailing: bool = False) -> str:
    """`src`에서 주석을 걷어낸 실행 코드만 반환한다.

    `comment_markers` 중 하나로 **시작하는 줄**을 버린다. `strip_trailing=True`면
    남은 줄의 후행 `//` 주석도 잘라낸다(단 `://`는 URL이므로 건드리지 않는다 —
    이 예외가 없으면 `https://…`가 들어간 줄이 절반만 남는다).

    ⚠️ 문자열 리터럴 안의 `//`까지 자르지는 **못한다**(정확히 하려면 파서가
    필요하다). 이 헬퍼의 목적은 "주석에 걸린 오탐 제거"까지이고, 그 이상이
    필요하면 AST/중괄호 깊이로 구간을 좁히는 쪽을 쓸 것
    (`test_trace_const_audit.py`가 AST를 쓰는 것과 같은 이유).
    """
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith(comment_markers):
            continue
        if strip_trailing and "//" in line and "://" not in line:
            line = line.split("//")[0]
        out.append(line)
    return "\n".join(out)


# ── 헬퍼 자체의 테스트 ────────────────────────────────────────────────
# 헬퍼가 조용히 망가지면 그걸 쓰는 모든 "없어야 한다" 검사가 **오탐 쪽으로**
# 무너진다(있어도 통과, 없어도 통과가 아니라 — 검사 자체가 무의미해진다).
def test_drops_whole_comment_lines():
    src = "// removed = 1\nkeep = 2\n  // also removed\n"
    # splitlines()+join이라 **후행 개행은 보존하지 않는다** — 검사 용도라
    # 의도된 동작이고, 기존 6개 구현도 전부 같았다(동작 불변).
    assert code_only(src) == "keep = 2"


def test_keeps_trailing_comments_by_default():
    """단순형 4곳의 동작 — 후행 주석은 **남긴다**."""
    assert code_only("keep = 2  // tail") == "keep = 2  // tail"


def test_strips_trailing_comments_when_asked():
    """강한형 2곳의 동작."""
    assert code_only("keep = 2  // tail", strip_trailing=True) == "keep = 2  "


def test_url_is_not_mistaken_for_a_trailing_comment():
    line = 'fetch("https://example.com/x")'
    assert code_only(line, strip_trailing=True) == line


def test_python_comment_marker_is_opt_in():
    src = "# gone\nkeep = 1\n"
    assert code_only(src) == src.rstrip("\n"), "기본값은 `#`을 건드리지 않는다"
    assert code_only(src, comment_markers=("//", "#")) == "keep = 1"


def test_the_actual_false_positive_it_exists_for():
    """다섯 번 겪은 오탐을 재현해 헬퍼가 실제로 막는지 본다."""
    src = ('// v5.291: `toggleAbcGateBreak()` 제거 — 강돌파 단계로 흡수됐다.\n'
           'const stageRow = [_abcChip("강돌파")];\n')
    assert "toggleAbcGateBreak" in src, "재현 전제가 깨졌다"
    assert "toggleAbcGateBreak" not in code_only(src)
