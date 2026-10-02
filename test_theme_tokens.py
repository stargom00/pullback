"""v5.304 — 라이트/다크 테마 토큰(static/index.html).

① 컴포넌트 스타일에 날 색(hex·rgb)이 남지 않는다 — 색은 테마 블록의 토큰으로만.
② 다크·라이트 두 벌의 키 집합이 같다 — 한쪽에만 있는 토큰은 그 테마에서 조용히
   다른 테마 값(또는 무효)으로 떨어진다.
③ 쓰이는 var(--x)는 전부 정의돼 있다.
④ 글자 토큰 대 배경 토큰 대비 ≥ 4.5:1 (두 테마). 실제 화면 대비는 Playwright 순회로 따로 쟀다.
⑤ 테마 결정 함수(themeFromStorage)를 production 원문 그대로 node로 실행 — 기본 라이트,
   'dark'만 다크, localStorage가 throw해도 라이트.

사보타주 확인(2026-09-30): 라이트 블록에서 --c-red-fg3 한 줄 삭제 →
test_both_themes_have_same_keys FAIL — 원복. ② .entry-sig .sig-note의 opacity:max(.7,…)를
opacity:.7로 되돌림 → test_text_opacity_goes_through_fade_floor FAIL — 원복.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

TEXT = (Path(__file__).resolve().parent / "static" / "index.html").read_text(encoding="utf-8")

DARK_SEL = ':root[data-theme="dark"]{'
LIGHT_SEL = ':root,:root[data-theme="light"]{'

HEX = re.compile(r"(?<![\w&])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})\b")
RGB = re.compile(r"rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*(?:,\s*[\d.]+\s*)?\)")


def _block(sel):
    assert TEXT.count(sel) == 1, f"{sel} 블록이 정확히 하나여야 한다"
    i = TEXT.index(sel)
    return TEXT[i:TEXT.index("\n}", i)]


def _vars(sel):
    return {k: v.strip() for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", _block(sel))}


# 날 색이 남아도 되는 곳(보고 대상 예외) — 여기 말고는 전부 토큰이어야 한다.
EXCEPTIONS = [
    (r'<meta name="theme-color"[^>]*>', "브라우저 주소창 색 — 첫 페인트 전 기본값, 토글 때 JS가 바꾼다"),
    (r"const THEME_META = \{[^}]*\};", "위 meta에 넣을 값 — CSS 변수를 meta에 못 넣는다"),
]


def _skip_spans():
    spans = []
    for rx in (r"/\*.*?\*/", r"<!--.*?-->"):
        spans += [(m.start(), m.end()) for m in re.finditer(rx, TEXT, re.S)]
    spans += [(m.start(), m.end()) for m in re.finditer(r"(?m)^[ \t]*//.*$", TEXT)]
    spans += [(m.start() + 1, m.end()) for m in re.finditer(r"(?m)[;{}),]\s*//[^\n'\"`]*$", TEXT)]
    for sel in (DARK_SEL, LIGHT_SEL):
        i = TEXT.index(sel)
        spans.append((i, TEXT.index("\n}", i)))
    for rx, _why in EXCEPTIONS:
        ms = list(re.finditer(rx, TEXT))
        assert len(ms) == 1, f"예외 {rx}가 정확히 한 곳이어야 한다(늘었으면 이유를 적고 목록에 추가)"
        spans.append((ms[0].start(), ms[0].end()))
    return spans


def test_no_raw_colors_outside_theme_blocks():
    spans = _skip_spans()
    left = []
    for rx in (HEX, RGB):
        for m in rx.finditer(TEXT):
            if not any(a <= m.start() < b for a, b in spans):
                line = TEXT.count("\n", 0, m.start()) + 1
                left.append(f"{line}: {m.group(0)}  …{TEXT[max(0, m.start() - 40):m.start()].splitlines()[-1][-40:]}")
    assert not left, "토큰 대신 날 색이 쓰였다:\n" + "\n".join(left[:30])


def test_both_themes_have_same_keys():
    d, l = _vars(DARK_SEL), _vars(LIGHT_SEL)
    assert len(d) >= 150, "다크 블록이 비었다"
    assert set(d) == set(l), f"다크에만: {sorted(set(d) - set(l))} / 라이트에만: {sorted(set(l) - set(d))}"
    # --fade-floor: opacity 흐림의 하한(색 아님). 다크 0 = 원래 흐림 그대로, 라이트 1 = 흐림 끔
    assert (d.pop("--fade-floor"), l.pop("--fade-floor")) == ("0", "1")
    for k, v in {**d, **l}.items():
        assert HEX.fullmatch(v) or RGB.fullmatch(v), f"{k}: 색이 아닌 값 {v}"


def test_text_opacity_goes_through_fade_floor():
    """글자를 opacity로 흐리면 라이트에서 4.5:1이 깨진다(보조색 4.9:1 × .55~.8). 흐림은
    max(.X,var(--fade-floor))로만 — 예외는 WCAG 대비 대상이 아닌 비활성 버튼과, 호버로 드러나는
    숨김 버튼뿐."""
    spans = [(m.start(), m.end()) for rx in (r"/\*.*?\*/", r"<!--.*?-->") for m in re.finditer(rx, TEXT, re.S)]
    spans += [(m.start(), m.end()) for m in re.finditer(r"(?m)^[ \t]*//.*$", TEXT)]
    raw = [TEXT[TEXT.rfind("}", 0, m.start()) + 1:m.end()].strip()   # 규칙 선택자부터
           for m in re.finditer(r"opacity:\s*0?\.\d+", TEXT) if not any(a <= m.start() < b for a, b in spans)]
    allowed = [s for s in raw if ":disabled{" in s or ".hide-btn{" in s]
    assert len(raw) == len(allowed) == 3, raw
    # v5.316: 홈 "오늘 할 일"·"후보" 카드 제거로 그 안의 2곳이 함께 사라져 75 → 73
    assert TEXT.count("var(--fade-floor))") >= 73


def test_no_color_token_defined_outside_theme_blocks():
    """테마 블록 밖의 :root에 색 토큰이 남으면 뒤에 나오는 쪽이 라이트 값을 덮는다."""
    for m in re.finditer(r"(?m)^:root\{", TEXT):
        blk = TEXT[m.start():TEXT.index("\n}", m.start())]
        colors = [k for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", blk) if HEX.fullmatch(v.strip()) or RGB.fullmatch(v.strip())]
        assert not colors, colors


def test_every_used_var_is_defined():
    defined = set(re.findall(r"(--[\w-]+)\s*:", TEXT))
    used = set(re.findall(r"var\((--[\w-]+)", TEXT))
    # v5.304 이전부터 정의 없이 쓰이던 이름 — 무효라 브라우저 기본값(투명 배경·currentColor
    # 테두리·기본 글꼴)으로 그려지고 있다. 정의하면 다크 화면이 바뀌어(범위 밖) 목록으로만 고정한다.
    # 새 미정의 이름이 생기면 FAIL.
    legacy_undefined = {"--accent", "--border", "--card", "--mono", "--rs"}
    assert not (used - defined - legacy_undefined), sorted(used - defined - legacy_undefined)


def _hex(v):
    v = v.lstrip("#")
    return [int(v[i:i + 2], 16) / 255 for i in (0, 2, 4)]


def _lum(c):
    f = lambda x: x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4
    r, g, b = map(f, c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _cr(a, b):
    x, y = sorted((_lum(_hex(a)), _lum(_hex(b))), reverse=True)
    return (x + 0.05) / (y + 0.05)


TEXT_TOKENS = ["--text", "--muted", "--n-text", "--n-sub", "--n-muted", "--n-accent-fg",
               "--px-up", "--px-down", "--up", "--green", "--amber", "--c-red-fg3", "--c-blue-fg4"]
BG_TOKENS = ["--bg", "--surface", "--surface2", "--n-bg", "--n-card", "--n-chip"]


@pytest.mark.parametrize("sel", [DARK_SEL, LIGHT_SEL])
def test_text_tokens_contrast(sel):
    v = _vars(sel)
    bad = [(t, b, round(_cr(v[t], v[b]), 2)) for t in TEXT_TOKENS for b in BG_TOKENS if _cr(v[t], v[b]) < 4.5]
    if sel == DARK_SEL:
        # 다크는 v5.304 이전 값 그대로(바꾸지 않는 게 요구사항) — 기존 미달 조합만 허용 목록으로 고정
        bad = [x for x in bad if x[:2] not in {("--muted", "--surface2")}]
    assert not bad, bad


def test_lowpoint_win_red_loss_blue_in_both_themes():
    """저점 탭 손익색: 수익=빨강 · 손실=파랑(한국식) — 라이트에서도 색상 계열 유지."""
    import colorsys
    for sel in (DARK_SEL, LIGHT_SEL):
        v = _vars(sel)
        hr = colorsys.rgb_to_hls(*_hex(v["--c-red-fg3"]))[0] * 360
        hb = colorsys.rgb_to_hls(*_hex(v["--c-blue-fg4"]))[0] * 360
        assert hr < 15 or hr > 340, (sel, hr)
        assert 200 < hb < 250, (sel, hb)
    fn = TEXT[TEXT.index("function renderLowpointTrack("):]
    assert fn.count("pnl > 0 ? 'var(--c-red-fg3)'") >= 2 and fn.count("< 0 ? 'var(--c-blue-fg4)'") >= 2


def _fn(name):
    i = TEXT.index(f"function {name}(")
    b = TEXT.index("{", TEXT.index(")", i))
    d = 0
    for k in range(b, len(TEXT)):
        d += {"{": 1, "}": -1}.get(TEXT[k], 0)
        if d == 0:
            return TEXT[i:k + 1]
    raise AssertionError(name)


@pytest.mark.parametrize("storage,want", [
    ("{getItem: () => null}", "light"),
    ("{getItem: () => 'dark'}", "dark"),
    ("{getItem: () => 'light'}", "light"),
    ("{getItem: () => 'purple'}", "light"),
    ("{getItem: () => { throw new Error('blocked'); }}", "light"),
])
def test_theme_from_storage_defaults_light(storage, want):
    if not shutil.which("node"):
        pytest.skip("node 미설치")
    js = f"const localStorage = {storage};\n{_fn('themeFromStorage')}\nconsole.log(JSON.stringify(themeFromStorage()));"
    p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout) == want


def test_theme_applied_before_stylesheet_and_toggle_exists():
    head_script = TEXT.index("applyTheme(themeFromStorage());")
    assert head_script < TEXT.index("<style>"), "첫 페인트 전에 테마를 정해야 깜빡이지 않는다"
    assert 'id="themeToggle"' in TEXT and 'onclick="toggleTheme()"' in TEXT
    assert ">라이트</span>/<span" in TEXT and ">다크</span>" in TEXT
    assert "localStorage.setItem('scannerTheme', theme)" in _fn("setTheme")
