# 눌림목 스캐너 (pullback scanner)

개인용 눌림목 스캐너 웹앱. 한국/미국 시장을 스캔해 눌림목 셋업을 찾고, 뉴스 기반 섹터 분석을 제공한다. Minervini/O'Neil/IBD/SEPA 방법론 기반.

- 배포: pullback2-production.up.railway.app
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
- 라이브 URL(pullback2-production.up.railway.app)은 Claude bash 네트워크 allowlist에 **없다** → curl 불가.
- `raw.githubusercontent.com`은 됨 → 배포 검증은 **레포의 raw 파일**로 한다.

---

## 개발 원칙 (Seulki)
- **발견한 문제는 경고가 아니라 실패로 만든다. 주석·INFO 출력·⚠️ 표시는 강제 장치가 아니다.** 판단 지점에서 "경고로 남길까 실패로 만들까" 고민되면 기본값은 **실패**. v5.60~v5.65에서 실제로 겪은 사례 3건: ① `_gate_risk_pct`에 "scanner.py와 반드시 같은 로직으로 유지"라는 동기화 주석만 있고 강제 장치가 없어서 실제로 어긋났음(계산식을 `scanner._risk_pct_at_gate()`로 공용화해서 물리적으로 한 곳에만 있게 고침). ② `test_trace_const_audit.py`가 float 리터럴 32건을 INFO 체크리스트로만 남겼는데 아무도 안 봄. ③ `test_trace_parity.py`가 셋업별 커버리지 0건을 print 경고(⚠️)로만 표시해서 3개 셋업(turnaround/breakout/boxbreak)이 미검증인 채로 CI가 계속 초록불이었음(`test_min_coverage_per_setup` hard FAIL로 전환). 사람이 로그를 따로 읽어야만 작동하는 장치는 결국 아무도 안 읽는다 — 자동으로 막을 수 있으면 막는다.
- **근본 원인 먼저.** 이상한 값이 나오면 방어 코드나 range 필터로 덮지 말고, 왜 그 값이 나왔는지부터 밝힌다. 재현으로 원인 확인 후 패치.
- 방어/안전망 코드(특히 외부 데이터 오염 대비)는 **근본 수정 위에 2차 레이어로만** 추가.
- **번들 수정 선호.** 패치 찔끔찔끔 배포 말고 묶어서. 데이터 소스 한계는 추측-패치-배포 반복 대신 처음에 선언.
- 백테스트 없는 승률 주장 금지. 💎적격 우위는 구조적 추론(더 타이트한 손절 = +1R 도달성)이지 검증된 승률이 아니다. 실제 답은 Seulki 저널의 적격/부적격 결과 누적에서 나온다.
- **새 `analyze_*` 함수나 헬퍼(trend_grade류) 추가 시, 내부 `rolling`/`iloc` 슬라이스가 실제 요구하는 최대 봉 수를 그 함수의 `min_bars` 게이트와 대조할 것.** 헬퍼 함수는 자기 게이트가 없고 호출부의 `min_bars`에 그대로 종속되므로("min_bars는 통과하는데 내부가 더 요구하는" 클래스, v5.32 감사에서 4건 발견), 헬퍼 docstring에 내부 요구 봉수를 명시해 호출부에서 바로 대조 가능하게 한다.
- **`scanner.py`의 CONFIG 값이나 함수 내부 판정 상수(임계값·배수·분기 로직)를 바꾸면, `app.py`의 `_trace_*`/`_gate_*` 진단 재현 함수에 같은 값의 사본이 있는지 반드시 확인할 것.** `/api/debug`의 `_trace_*` 함수들은 각 탭의 실제 게이트 순서를 "재현"하려고 만든 별도 코드라, `cfg[...]`로 직접 참조하지 않고 리터럴로 복사해둔 곳은 scanner.py만 고치면 조용히 낡는다(v5.60 `slope_floor`가 `_trace_pullback`에 사본으로 남아 동기화가 안 됐던 사고, `docs/rs_definition_and_slope_investigation.md` 6절). 원칙: **CONFIG 딕셔너리에 있는 값이면 반드시 `cfg[...]`로 참조**(리터럴 복사 금지) — scanner.py 자신도 지역 리터럴로 쓰는 값(예: 손절 배수 0.97/0.98/0.15)이라 CONFIG로 뺄 수 없는 경우만 예외로 허용하고, 그 자리에 "scanner.py의 X와 동기화 필요" 주석을 남긴다. `test_trace_const_audit.py`가 `_trace_*` 함수의 리터럴 삼항식(`X if 조건 else Y` 둘 다 숫자 리터럴 — 이번 사고와 같은 모양)을 자동으로 잡아주지만, 그 외 단일 리터럴 복사는 사람이 직접 대조해야 한다(파일이 INFO로 목록만 뽑아줌, 완전자동 판정은 구조상 불가능 — 어떤 리터럴이 scanner.py의 어느 함수와 짝인지는 코드만 봐서는 알 수 없기 때문). `test_trace_parity.py`(v5.63)가 실제 KR/US 종목으로 `_trace_*`와 `analyze_*`를 같이 돌려 stop/risk_pct 값 자체가 일치하는지 추가로 검증한다 — 상수 이름/위치를 안 보고 동작으로 비교해서 위 AST 감사의 한계(리터럴이 몇 개든 어디 있든 무관)를 보완한다.
- **백테스트 수치(z값, EV, n) 갱신 시 절차 — 위 CONFIG 동기화 원칙을 문서·UI 하드코딩 수치에도 그대로 적용한다.**
  1. `JONGGA_BACKTEST_NOTE` 등 서버 상수를 먼저 고친다
  2. grep으로 문서·UI에 하드코딩된 구수치가 남았는지 확인한다(예: `grep -rn 'z=4.28' .`)
  3. 발견되면 상수 참조로 바꾸거나, 참조 불가한 서술 문서면 직접 갱신한다
  2026-09-02 라벨-의미 전수조사에서 종가베팅 수치가 6곳(static/index.html 5곳 + GUIDE.md 1곳)에 흩어져 있던 것이 발견된 데 따른 절차(v5.153에서 4곳은 서버 상수 참조로 구조 정리, GUIDE.md는 직접 갱신).
- **`_trace_*` 사본(app.py) 보류 이유와 제거 트리거.** `analyze_*`가 게이트 탈락 시 `None`만 반환해 진단 정보가 안 남는 게 사본이 존재하는 이유다(v5.39 핫패스 성능 선택 — 구조적 제약은 아님). `analyze_*`에 `trace: list | None = None` 선택적 파라미터를 추가하면(기본 None → 실제 스캔 동작 무변화) 사본을 없앨 수 있지만, 실거래 함수 5개의 게이트 본문을 건드려야 해서 리스크가 크다 — `test_trace_parity.py`가 값 불일치를 잡아주는 한 지금은 보류(v5.63 결정, `docs/rs_definition_and_slope_investigation.md` 8절). **다음 중 하나라도 해당되면 그때는 제거를 실행할 것**: ① 차등 테스트(`test_trace_parity.py`)가 못 잡는 종류의 불일치가 한 번이라도 실제로 발생 ② `_trace_*`에 새 진단 항목을 추가해야 해서 사본에 또 손대야 하는 시점. 실행 방법: `analyze_*`에 `trace` 파라미터 추가 → 게이트 탈락 지점마다 사유를 `trace`에 append → app.py의 `_trace_*` 삭제.
- **외부 유료 API(Claude API 등)를 호출하는 엔드포인트는 반드시 쿨다운·진행중 잠금·일일 한도를 함께 구현할 것.** 셋 중 하나라도 빠지면 반복 클릭·재시도 스크립트·자동+수동 트리거 경합이 그대로 풀프라이스 호출로 나간다. 2026-08 비용 급증 사건이 근거: `money_flow_report.py`(2026-08-26 신설)를 호출하는 `/api/moneyflow/{market}/run`("🔄 재실행" 버튼)에 이 셋 중 아무것도 없어서, 개발 중 반복 클릭만으로 6일간 Anthropic API $48(웹서치 338회 포함, 예상 일일비용의 20배)이 나갔다 — `theme_map.py`/`macro_calendar.py`도 같은 문제가 있었음(v5.126에서 셋 다 추가, `docs/kr_theme_leader_reignition.md`류가 아니라 app.py 자체의 엔드포인트 가드 문제이므로 별도 조사 기록 없이 코드에만 반영돼 있음 — `_moneyflow_manual_running`/`_moneyflow_manual_last_run`, `_macro_calendar_task_running`+수동 쿨다운, `_theme_map_generating` 참고). 새 Claude API 호출 엔드포인트를 추가할 때 이 세 가드를 빠짐없이 넣을 것.
- **유료 API 가드는 (a) 함수 레벨에 두고 (b) 상태를 영속 저장할 것 — HTTP 라우트에만 두면 내부(스케줄러 등) 호출이 그대로 우회한다.** 2026-08-31/09-01 두 차례 사고가 근거. 08-31: `POST /api/moneyflow/{market}/run`에만 쿨다운(`_moneyflow_manual_running`/`_moneyflow_manual_last_run`)을 달았는데, 스케줄러(`_warm_market`)는 이 라우트를 거치지 않고 `_run_money_flow_bg`를 직접 호출해 애초에 이 가드를 볼 일이 없었다. 09-01: 그래서 스케줄러 쪽에 별도로 "오늘 이미 실행했다" 게이트(`_moneyflow_warmed`)를 뒀는데, 이게 **프로세스 메모리 dict**라 Railway 컨테이너가 재시작될 때마다(원인 미상, 약 19분 주기 관측) 리셋되어 하루 수십 회씩 Claude API를 재호출했다 — 방어 위치(라우트 전용)와 방어 상태(비영속)가 각각 따로 뚫린 두 번째 사고. 근본 수정(v5.141): `money_flow_report.generate_report()`/`theme_map.generate_theme_map()`/`macro_calendar.generate_calendar()` **함수 자신이** 마지막 성공 시점·쿨다운을 파일(영구 볼륨, JOURNAL_DIR→/data→앱폴더 우선순위)에 기록·재확인하도록 이식 — 어느 경로(스케줄러/수동 HTTP/향후 생길 새 호출부)로 불려도 우회 불가능. 추가로 세 모듈이 공유하는 `api_call_guard.py`(app.py 미의존, 자체 파일 영속)가 하루 총 20회 상한을 전역으로 강제(14회 경고·20회 차단, `GET /api/apiguard/status`로 노출 — 이 레포엔 텔레그램 발송 코드가 없어 얼마냐봇(별도 레포)이 폴링해서 보내야 실제 발송됨). **새 유료 API 호출부를 추가할 때: ① 가드 상태는 반드시 파일에 영속(메모리 dict 금지) ② 가드 체크는 호출하는 함수 자신의 최상단에 둘 것(라우트에만 두지 말 것) ③ `api_call_guard.check_and_count()`를 호출 직전에 반드시 거칠 것.**
- **필드/상수 이름이 실제 의미(특히 단위·기준)와 어긋나면 이름을 바꾸거나 명시적 보조 필드를 추가한다 — 주석만 남기면 잊힌다.** 2026-09-02 하루에만 같은 유형의 어긋남을 여러 번 겪음(RSI≥50, A/B/C/D 등급, strategy_map 등). 구체 사례: `money_flow.py`의 `streak_days`는 이름 그대로 "연속 일수"였는데, 돈의흐름 실행 주기를 매일→주 1회로 바꾸면서(v5.147, 비용 절감) 1 증가가 실제로는 "1주"를 의미하게 됐다 — 이름은 그대로 두고 넘어갔으면 나중에 "streak_days=3"을 보고 "3일 연속"으로 오독하는 사고가 반드시 났을 것. 조치: `streak_days`→`streak_periods`(주기 중립적 이름)로 개명 + `streak_unit`("week") 필드를 별도로 추가해 현재 단위를 항상 명시 — 이름과 값을 같이 봐야만 의미가 정해지는 상태를 없앴다. **판단 기준**: 어떤 필드의 이름에 시간·단위·등급 기준이 박혀 있는데(예: `_days`, `_pct`, `_score`, 등급 문자) 그 기준이 설정값 변경·리팩터로 달라질 수 있다면, "언젠가 어긋날 수 있다"가 아니라 지금 바로 이름/보조필드로 고정한다.

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
- **`atr_pct_high`(v5.154 이전 이름 `vol_high` — 거래량이 아니라 ATR%인데 이름이 volume처럼 읽혀 개명, 라벨-의미 감사 `docs/label_semantics_audit_2026-09-02.md`)/`atr_tight`(진입신호등 6번째 체크: ATR 대비 손절폭 변동성 경고)가 `analyze()`(눌림목)에만 있고 `badge_fields()` 공용 헬퍼엔 없음.** `analyze_breakout`/`analyze_imminent`/`analyze_pattern`(v5.37에서 badge_fields는 추가함) 전부 이 2개 필드가 안 채워져서 `entrySignal()`의 ATR 체크가 스킵됨 — 패턴 탭만의 문제가 아니라 눌림목 제외 전 탭 공통. 의도적 설계(눌림목만 이 체크가 필요하다고 판단)인지 단순 누락인지 코드에 근거 주석이 없어 판단 보류 — 필요해지면 `badge_fields()`로 옮기거나 각 탭에 개별 추가할지 결정할 것.
- **패턴 탭에서 `target_basis`가 항상 `2R`인 이유**: 리스크%가 넓어(중앙값 21%) 최소 2R 바닥이 측정이동 목표보다 높게 걸림. 109/109 전건 확인. 측정이동 분기는 패턴 탭에서 사실상 죽은 코드. 눌림목/돌파 탭은 리스크가 좁아 정상 작동할 가능성 있으나 미확인.
- **`/api/debug/{ticker}`의 modes/게이트추적 RS 판정: 콜드캐시일 때만 80 근사치 폴백, 캐시 웜이면 실제 rs_ranks 사용 (v5.61).** 유니버스 전체 캐시(`_fetch_market_data("all")`)가 따뜻하면 거기서 실제 rs_ranks/rs_moms를 가져오고, 콜드일 때만 80 고정 근사치로 폴백하며 이 경우 reasons에 `⚠️ RS 근사치(...)`로 명시됨. rs_min이 75~80 사이인 경계 종목(breakout/boxbreak는 rs_min=75, imminent는 rs_min=80 — scanner.py BREAKOUT_CONFIG/BOXBREAK_CONFIG/IMMINENT_CONFIG 실제값, 2026-09-02 확인. v5.61 당시엔 85였으나 이후 v5.69/v5.70에서 탭별로 재조정된 뒤 이 문장이 안 고쳐져 있었음 — 라벨-의미 감사에서 발견, `docs/label_semantics_audit_2026-09-02.md`)은 콜드캐시 상황에서만 이 근사치로 인해 디버그 패널과 실제 스캔 결과가 어긋날 수 있음.
- **GitHub Actions push 트리거가 원인 미상으로 작동 안 함** (Actions permissions 'Allow all' 확인, 빌링 정상, 워크플로 활성, main 브랜치 확인, Ruleset 없음 — 전부 배제). workflow_dispatch 수동 실행은 정상(run #1, 46초 성공). daily schedule로 대체. 큰 변경 후에는 Actions 탭에서 Run workflow 수동 실행 권장. schedule cron은 등록 후 첫 실행까지 최대 하루 걸리고 GitHub 부하에 따라 밀릴 수 있음 — 정시에 안 도는 건 정상.
- **배포 직후 500/Application failed to respond가 잠깐 뜨는 현상 — Railway 컨테이너 교체 중 접속 시 발생.** 2026-08-30(v5.92), 2026-08-31(v5.115) 두 차례 관측, 둘 다 로컬 재현 불가·1~2분 후 자동 복구. 대응: 코드 조사 전에 1~2분 기다렸다 새로고침 먼저. 그래도 지속되면 Railway Deployments 로그 확인.
- **`naver_kr.to_code()`가 티커 접미사(.KS/.KQ)를 검증하지 않는다 — 잘못된 접미사로 조회해도 데이터가 정상 반환된다.** `to_code()`는 `ticker.split(".")[0]`으로 접미사를 그냥 잘라내기만 하고 실제로 어느 시장인지는 안 본다(Naver `siseJson` API 자체가 6자리 코드만 받고 시장 구분을 안 씀 — 코드가 KOSPI/KOSDAQ 통틀어 유니크해서 우연히 항상 "정답"이 나옴). 그래서 `244920.KQ`(실제는 KOSPI, `.KS`가 맞음)로 `fetch()`/`fetch_history()`를 호출해도 에러 없이 에이플러스에셋 데이터가 그대로 나온다 — 2026-09-01 유니버스 조사 중 이 착오로 "유니버스에 없다"는 잘못된 결론을 냈다가 정정한 사례로 발견(`docs/kr_universe_turnover_pagination_investigation.md`). **`get_universe()`가 반환하는 dict의 key(정확한 접미사)와 다른 접미사로 티커를 다루는 코드가 있으면 같은 착오가 조용히 숨어있을 수 있다** — 의심되면 `t in get_universe("kr")`로 정확한 키 존재 여부부터 확인할 것. 수정(예: to_code가 실제 시장과 불일치 시 경고/보정)은 아직 안 함 — 사용자 지시로 기록만.
- **`/data` 파일을 스크립트로 직접 수정할 때, 열려있는 브라우저 탭이 그 파일의 옛 내용을 메모리에 들고 있다가 나중에 통째로 덮어쓸 수 있다 — 단, 이 위험은 `journal_user.json`(일지)에만 해당하고 "앱 재시작"으로는 안 고쳐진다.** 2026-09-02 저널 UTC/KST 마이그레이션(`scripts/maintenance/2026-09-02_journal_date_kst_migration.py`) `--apply`가 정상 실행됐는데도 파일이 원래대로 돌아간 사고로 발견. 원인은 **서버가 아니라 브라우저 탭**: `static/index.html`의 `journalCache`(전역 JS 변수)는 탭이 열릴 때 `loadJournalFromServer()`로 딱 한 번 로드된 뒤 그 탭이 살아있는 내내 메모리에 남고, `setJournal()`(감시 등록 `quickWatch`/`updateTracking`의 자동 가격갱신/일지 수정 `saveJournal`·`markEntered`·`markMissed`·`revertToPending`·`reopenRow`·`markWatchEntered/Missed/Closed`·`reopenWatch`·`cleanupExpiredWatches`·`savePartial`·`saveEdit`·`updateResult`·`delJournal`·전체삭제 등 19곳)가 호출될 때마다 그 메모리 스냅샷을 `POST /api/journal`로 통째로 보내 파일을 무조건 덮어쓴다(서버 쪽 `save_journal()`은 버전/타임스탬프 비교 없이 받은 배열을 그대로 씀). `updateTracking()`은 페이지 로드 시·주기 폴링으로 **사용자 조작 없이도** 자동 실행되므로, 탭을 열어만 둬도(일지 탭이 아니어도) 걸릴 수 있다. **서버(`load_journal()`/`app.py`의 다른 JSON 스토어 전부: `_load_reignition_watch`/`theme_map._load` 등)는 매 호출마다 디스크에서 새로 읽고 그 자리에서 바로 써서 이 위험이 없음(확인 완료)** — 캐시는 오직 브라우저 쪽에만 있다. **안전한 절차**: 스크립트로 `/data` 파일을 직접 고치기 전에 그 파일을 다루는 열린 브라우저 탭을 전부 닫고(또는 최소한 그 세션에서 아무 조작도 안 하고), 수정이 끝난 뒤 새로고침해서 다시 연다 — Railway 서버 프로세스 재시작은 이 문제와 무관하며 필요하지도 않다. **이번에 조사한 볼륨 정리 대상 3종(`kr_universe_v6_*.json`/`datacache_*.pkl`/`moneyflow_warmed_*.marker`)은 전부 이 위험이 없음**(전부 "현재 시점"만 가리키는 인메모리 캐시라 지워진 과거 파일을 되살려 쓰는 경로 자체가 없음, 확인 완료) — 이 주의사항은 `journal_user.json`에만 해당.

---

### 돈의흐름(money flow) 리포트 — 주 1회 전환 (v5.147)
- 비용 절감 목적(월 ~$50 → ~$8, 호출당 ~113k input+13k output, 실제 활용도가 참고용이라 매일 필요 없다는 판단). 매일→**토요일 09:00 KST 이후 주 1회**로 전환.
- `_maybe_run_weekly_money_flow()`(app.py, 스케줄러 루프에서 4분마다 체크)가 트리거. `daykey`는 "토요일 날짜"가 아니라 `_last_trading_daykey()`로 역산한 **그 주의 실제 마지막 거래일**(보통 금요일, 공휴일이면 그 이전) — 기존 마커(`_moneyflow_warmed_*.marker`)/저장 함수(`money_flow.save_report_markdown` 등)를 형식 변경 없이 그대로 재사용(값이 주 1회만 생기는 날짜라 ISO 주차 같은 새 포맷 불필요).
- 수동 재실행 버튼(`POST /api/moneyflow/{market}/run`)은 이 마커/스케줄과 완전히 독립된 경로(자체 120초 쿨다운+진행중 잠금) — 서로 간섭 안 함, 평일에 수동 실행하면 그 날짜가 최신으로 잡혀 다음 토요일 전까지 캘린더에 우선 표시됨(정상).
- **`streak_days`→`streak_periods`+`streak_unit`("week") 개명** — 위 "필드/상수 이름" 원칙 참고. 읽는 곳: `_warm_market`의 theme_map 자동생성 트리거(`streak_periods>=2` = "2주 연속"), `get_calendar()`의 강세테마×스캐너 교집합, `docs/money_flow_prompt.md`(Claude 프롬프트 본문 — 리포트 생성에 직접 쓰이는 파일이라 "일" 관련 서술 전부 "주"로 수정), 화면 표시(app.py `/moneyflow` 디버그 페이지 + `static/index.html` 동일 코드, `streak_unit` 값에 따라 "주"/"일" 동적 표시).
- 전환 과도기: 2026-09-02(수) 배포 이전에 이미 그 주 월~수 매일 마커가 생겼던 것은 그대로 남아 무해(용량 무시할 수준, 안 쓰이게 될 뿐) — 이번 주(9/5 토)에 한 번 더 돌고, 다음 주(9/12 토)부터 완전히 주 1회로 안정화.
- `api_call_guard.py`의 전역 일일 상한(20회, `DAILY_LIMIT`)은 그대로 유지 — money_flow_report/theme_map/macro_calendar 셋이 공유하는 상한이라 moneyflow 호출량 감소를 이유로 낮추면 나머지 둘의 정상 호출까지 조여질 뿐 실익이 없다고 판단(2026-09-02 결정).

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
