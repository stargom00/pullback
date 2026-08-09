# 눌림목 스캐너 (pullback scanner)

개인용 눌림목 스캐너 웹앱. 한국/미국 시장을 스캔해 눌림목 셋업을 찾고, 뉴스 기반 섹터 분석을 제공한다. Minervini/O'Neil/IBD/SEPA 방법론 기반.

- 배포: pullback-production.up.railway.app
- 레포: stargom00/pullback
- 배포 흐름: GitHub push → Railway 자동 배포

---

## ⚠️ 배포 전 반드시 지킬 것 (하드하게 배운 것들)

### 1. 서빙되는 파일은 `static/index.html`이다
`app.py`의 `@app.get("/")`는 **`static/index.html`을 서빙한다.** 루트의 `index.html`은 아무도 안 보는 좀비 파일(삭제 대상).
**모든 UI 수정은 `static/index.html`에 해야 한다.** 루트 index.html 편집하면 라이브에 안 뜬다. (v4.58 base 배지가 "안 보인다"던 사건의 원인.)

### 2. 버전 스탬프를 항상 올린다
- `app.py`의 `VERSION` 문자열(`grep -n '^VERSION' app.py`로 찾을 것 — changelog가 계속 늘어나 라인 번호가 매 버전 밀림)을 올리고 `[변경 이력]` 헤더에 항목 추가.
- UI 버전 배지는 `static/index.html`의 `<span id="verBadge">`에 하드코딩 + JS가 스캔 완료 시 `app.py`의 `data.version`으로 덮어씀. **둘 다 같이 올려야** 새로고침마다 old→new 깜빡임이 안 생긴다.
- Seulki는 이 배지로 배포 성공을 확인함. 안 올리면 코드가 나가도 배지가 옛날 버전으로 남음.

### 3. 보이는 것 바꿨으면 static/index.html도 올린다
`scanner.py`/`app.py`는 API로 데이터를 주지만, **렌더링은 `static/index.html`이 한다.** 카드/배너에 새 배지·필드 추가했는데 .py만 올리면 화면에 조용히 안 뜬다.

### 4. git 커밋 메시지는 따옴표 없는 짧은 영어로
따옴표/오타로 커밋이 반복 실패한 이력 있음. `git commit -m short english message` 형태로.

---

## 검증 방법
- 라이브 URL(pullback-production.up.railway.app)은 Claude bash 네트워크 allowlist에 **없다** → curl 불가.
- `raw.githubusercontent.com`은 됨 → 배포 검증은 **레포의 raw 파일**로 한다.

---

## 개발 원칙 (Seulki)
- **근본 원인 먼저.** 이상한 값이 나오면 방어 코드나 range 필터로 덮지 말고, 왜 그 값이 나왔는지부터 밝힌다. 재현으로 원인 확인 후 패치.
- 방어/안전망 코드(특히 외부 데이터 오염 대비)는 **근본 수정 위에 2차 레이어로만** 추가.
- **번들 수정 선호.** 패치 찔끔찔끔 배포 말고 묶어서. 데이터 소스 한계는 추측-패치-배포 반복 대신 처음에 선언.
- 백테스트 없는 승률 주장 금지. 💎적격 우위는 구조적 추론(더 타이트한 손절 = +1R 도달성)이지 검증된 승률이 아니다. 실제 답은 Seulki 저널의 적격/부적격 결과 누적에서 나온다.
- **새 `analyze_*` 함수나 헬퍼(trend_grade류) 추가 시, 내부 `rolling`/`iloc` 슬라이스가 실제 요구하는 최대 봉 수를 그 함수의 `min_bars` 게이트와 대조할 것.** 헬퍼 함수는 자기 게이트가 없고 호출부의 `min_bars`에 그대로 종속되므로("min_bars는 통과하는데 내부가 더 요구하는" 클래스, v5.32 감사에서 4건 발견), 헬퍼 docstring에 내부 요구 봉수를 명시해 호출부에서 바로 대조 가능하게 한다.
- **`scanner.py`의 CONFIG 값이나 함수 내부 판정 상수(임계값·배수·분기 로직)를 바꾸면, `app.py`의 `_trace_*`/`_gate_*` 진단 재현 함수에 같은 값의 사본이 있는지 반드시 확인할 것.** `/api/debug`의 `_trace_*` 함수들은 각 탭의 실제 게이트 순서를 "재현"하려고 만든 별도 코드라, `cfg[...]`로 직접 참조하지 않고 리터럴로 복사해둔 곳은 scanner.py만 고치면 조용히 낡는다(v5.60 `slope_floor`가 `_trace_pullback`에 사본으로 남아 동기화가 안 됐던 사고, `docs/rs_definition_and_slope_investigation.md` 6절). 원칙: **CONFIG 딕셔너리에 있는 값이면 반드시 `cfg[...]`로 참조**(리터럴 복사 금지) — scanner.py 자신도 지역 리터럴로 쓰는 값(예: 손절 배수 0.97/0.98/0.15)이라 CONFIG로 뺄 수 없는 경우만 예외로 허용하고, 그 자리에 "scanner.py의 X와 동기화 필요" 주석을 남긴다. `test_trace_const_audit.py`가 `_trace_*` 함수의 리터럴 삼항식(`X if 조건 else Y` 둘 다 숫자 리터럴 — 이번 사고와 같은 모양)을 자동으로 잡아주지만, 그 외 단일 리터럴 복사는 사람이 직접 대조해야 한다(파일이 INFO로 목록만 뽑아줌, 완전자동 판정은 구조상 불가능 — 어떤 리터럴이 scanner.py의 어느 함수와 짝인지는 코드만 봐서는 알 수 없기 때문).

---

## 핵심 로직 메모

### 💎적격 vs score (혼동 주의)
- **score** = 셋업 예쁨 + 모멘텀. 베이스짧음·손절폭10.9%여도 99 가능.
- **💎적격** = 생존 필터 = 탄탄베이스 + RS90 + 손절폭 적정.
- 약세장에선 구조상 적격 86이 부적격 99보다 나을 수 있음(타이트한 손절). 단 이건 논리적 추론이지 백테스트 아님.

### ATR 상대 손절폭 (v4.67)
- `stop_wide` 판정 = `risk_pct > atr_pct × 1.5` (atr_pct = atr/close×100). 고정 5%US/7%KR을 대체 → 고ATR 미국주가 적격에서 구조적으로 빠지던 문제 해결.
- ATR 무효(<1%)일 때만 고정 %로 폴백.
- 🚫손절폭넓음 배지와 bear_ok(💎) 둘 다 적용. `badge_fields`가 atr_pct + 동적 stop_limit_pct 반환.
- **핵심 통찰:** "타이트함"은 손절폭/ATR 비율이지 절대 %가 아니다.

### `_risk_hard_ok` loosen-only ATR 완화 (v5.40)
- 한도 = `max(고정 US8%/KR12%, min(ATR%×1.5, 15%))`. `stop_wide`와 같은 ATR×1.5를 재사용(새 배수 발명 안 함). 저ATR 종목은 `max()`라 기존 통과분 영향 없음 — 순수 loosen-only(전체 유니버스 실측: 4탭 전부 탈락 신규 발생 0건).
- 절대 상한 15% — ATR이 커도 무제한 완화는 게이트 무력화(예: 리스크 30%대 종목이 "고ATR"이라는 이유로 통과하면 안 됨).
- **게이트와 배지가 같은 ATR×1.5를 쓰므로, 4개 게이트 탭(pullback/breakout/boxbreak/imminent)에서 `stop_wide`는 구조적으로 표시되지 않음(게이트를 통과한 결과는 정의상 `risk_pct ≤ ATR%×1.5`를 만족하므로). 의도된 동작 — turnaround/pattern 탭(이 게이트를 안 씀)에서는 계속 정상 작동.

### `_risk_hard_ok`의 판정 기준 = 카드 표시 risk_pct와 다를 수 있다 (v5.41로 pullback만 남음)
- `_risk_hard_ok(rrb, is_kr, pivot=...)`는 `pivot`이 주어지면 **피벗→손절** 기준으로 risk%를 재계산해서 판정한다. 카드에 표시되는 `risk_pct`(`_rr_block`이 반환, `entry` 기준)와 분모가 다르면 두 값이 크게 벌어질 수 있다.
- **pullback**: 여전히 `pivot=pivot`으로 호출(의도적 유지) — `_risk_hard_ok` docstring에 문서화된 이유(Case13: 돌파일 종가 기준으로 판정하면 정상 셋업이 잘림) 때문. 카드(`entry=close` 기준)와 게이트(피벗 기준) risk%가 다를 수 있음 — `/api/debug`의 "게이트기준_실제피벗"에 둘 다 표시됨.
- **breakout/boxbreak**: v5.41부터 `pivot` 인자 없이 호출 → `rrb["risk_pct"]`(= entry=close 기준, 카드와 동일값)를 그대로 씀. 코드 자체 주석("이미 돌파한 상태 → 실제 진입은 현재가")과 일치시킨 것. boxbreak는 `stop=pivot×0.97`로 손절이 피벗에 고정돼 있어서, 연장(ext)이 클수록 피벗 기준 risk%는 작게 유지되고 close 기준만 커지는 구조 — pivot 기준으로 판정하면 이미 크게 연장된 추격 진입도 하드게이트를 쉽게 통과했음(051160.KQ: 피벗대비 risk 3.79% vs 실제 29.0%). boxbreak엔 breakout에 있던 `extended_max`(12%)도 없었어서 이중으로 안 걸렸음 — v5.41에서 `BOXBREAK_CONFIG["extended_max"]=0.12` 신설.
- **imminent**: `_rr_block` 호출에 `entry=None`이라 애초에 `rrb["risk_pct"]`가 피벗 기준으로 계산됨 — `pivot=pivot`을 넘겨도 카드와 항상 일치. 손댈 필요 없음.

### 알려진 설계 갭 (미변경, 검토 대상)
- `analyze_imminent`(돌파임박) 추세 게이트가 `close >= ma200` AND `ma20 > ma60`뿐 → 단기 이평 아래로 깊게 눌린 종목도 통과함. Minervini 템플릿은 50일선 위를 요구. `price > ma20(or ma60)` 조건 추가 검토 중.
- **fetch 730일(KR)/2y(US) 확대(v5.28) 후 돌파/박스돌파/돌파임박 탭 회귀 미검증.** 눌림목·추세전환·ABC·`off_high_pct`·`rs_raw_score`·`count_bases_since_bottom` 등은 전체 유니버스 재현으로 회귀 없음을 확인했지만(400일→730일 hit 건수·순위 완전 동일), 이 3개 탭은 검증 당일 시세에 통과 종목이 0건이라 `max_off_high` 게이트가 실제로 걸러본 적이 없다. 해당 탭에 hit이 나오는 날 다시 확인 필요.
- **ABC(문서 방식) 탭 — 조사 후 보류.** 매집봉(+15%일봉)→돌파→눌림재터치 조건이 같은 날·같은 시총유니버스 무작위 대조군보다 15% 도달률이 낮게 나옴(선별력 없음이 아니라 역선택 방향, CI 거의 안 겹침). 조건 단계별 분리·진입타이밍 시프트·30봉 윈도우까지 재검증해도 뒤집히지 않음. 이 조사에서 나온 KR 시장 연도별(2021~2026) 기준 도달률표는 다른 조건/탭 검증할 때 재사용 가치 있음. 전체 근거·수치·재검토 시 참고사항은 `docs/abc_doc_style_tab_investigation.md`. (조사 중 KR 유니버스 1900일 통일안도 검토했으나 탭이 보류돼 미적용 — 730→1900일 회귀 0건 확인 자체는 방법론으로 남겨둠.)
- **price_ago 클램프 수정(v5.32)으로 상장 200~252봉 종목의 RS가 3분기(9개월) 기준으로 바뀜.** 이 코호트(KR 11/US 17종목, 각 시장 0.7~0.8%)의 순위 변동은 의도된 것(상장 초기가를 "12개월 전 가격"으로 오인하던 왜곡 제거)이지만, 3분기 재정규화 쪽이 4분기 클램프보다 실제로 더 정확한 신호인지는 백테스트 전까지 미결 — 이 코호트가 이후 눈에 띄게 이상하게 랭크되면 재검토 대상.
  - **신규 상장주 RS가 이상해 보이면 `/api/debug/{ticker}`의 `rs_quarters_used`부터 확인** (v5.34). 3이면 위 3분기 재정규화 코호트, 4면 정상 4분기 점수 — 원인 구분이 여기서 바로 됨.
- **`vol_high`/`atr_tight`(진입신호등 6번째 체크: ATR 대비 손절폭 변동성 경고)가 `analyze()`(눌림목)에만 있고 `badge_fields()` 공용 헬퍼엔 없음.** `analyze_breakout`/`analyze_imminent`/`analyze_pattern`(v5.37에서 badge_fields는 추가함) 전부 이 2개 필드가 안 채워져서 `entrySignal()`의 ATR 체크가 스킵됨 — 패턴 탭만의 문제가 아니라 눌림목 제외 전 탭 공통. 의도적 설계(눌림목만 이 체크가 필요하다고 판단)인지 단순 누락인지 코드에 근거 주석이 없어 판단 보류 — 필요해지면 `badge_fields()`로 옮기거나 각 탭에 개별 추가할지 결정할 것.
- **패턴 탭에서 `target_basis`가 항상 `2R`인 이유**: 리스크%가 넓어(중앙값 21%) 최소 2R 바닥이 측정이동 목표보다 높게 걸림. 109/109 전건 확인. 측정이동 분기는 패턴 탭에서 사실상 죽은 코드. 눌림목/돌파 탭은 리스크가 좁아 정상 작동할 가능성 있으나 미확인.
- **`/api/debug/{ticker}`의 modes/게이트추적이 rs_rank=80 고정 근사치로 판정됨.** 유니버스 전체 없이 종목 하나만으로는 실제 백분위 RS를 못 구해서 나는 한계(v5.39 트레이스 재작성에서도 그대로 유지). rs_min이 80~85 사이인 경계 종목(imminent/breakout/boxbreak는 rs_min=85)은 이 근사치 때문에 디버그 패널에서만 rs_min 탈락으로 뜨고 실제 스캔에선 통과할 수 있음(또는 반대). 경계 종목 판단 시 참고.
- **GitHub Actions push 트리거가 원인 미상으로 작동 안 함** (Actions permissions 'Allow all' 확인, 빌링 정상, 워크플로 활성, main 브랜치 확인, Ruleset 없음 — 전부 배제). workflow_dispatch 수동 실행은 정상(run #1, 46초 성공). daily schedule로 대체. 큰 변경 후에는 Actions 탭에서 Run workflow 수동 실행 권장. schedule cron은 등록 후 첫 실행까지 최대 하루 걸리고 GitHub 부하에 따라 밀릴 수 있음 — 정시에 안 도는 건 정상.

---

## 아키텍처 요약
- 스택: FastAPI(app.py) + scanner.py, 프론트는 static/index.html(단일 파일).
- 저널 저장: Railway 볼륨 마운트 `/data`.
- RS 랭킹: IBD 가중, 벤치마크 상대 백분위. (0.4×1mo + 0.4×3mo + 0.2×6mo + 0.1×9mo + 0.1×12mo)
- Minervini 추세 템플릿 8조건 구현. **단 섹터강도 필터·EPS 필터는 없음.**
- 탭: 돌파/급등/눌림목/섹터요약/패턴(컵앤핸들·깃발·더블바닥)/역ETF.
- 시장별 리스크 임계치: KR 12%, US 8%.
- `/api/ma/{ticker}` 엔드포인트를 얼마냐봇이 사용.
- `/api/debug` = '왜 안 잡혔나' 진단 패널 (탈락 핵심사유 자동 판정, 수평저항 touch-count).
- 대기(pending) 저널은 가격이 피벗 도달 시 자동으로 진입(entered)으로 전환됨(설계상). 순수 추적은 관찰 사용, 안 잡은 트레이드는 무산 처리해야 R 통계가 깨끗함.

---

## 연동
- 얼마냐봇(Telegram 알림 봇)이 `/api/ma/{ticker}` 사용. 봇 상태는 in-memory라 재배포 시 리셋됨.

---

## Git 워크플로
- 개인 전용 레포, 협업자 없음 — **별도 브랜치/PR 없이 main에서 바로 작업하고 push한다.**
- Railway는 main만 보고 자동 배포하므로, 다른 브랜치에 머물러 있으면 배포가 트리거되지 않는다.
