# 🔥 급등 관찰 탭 — 임시 실험 (v5.228)

## 상태

- **시작일**: 2026-09-09 (v5.228 배포일)
- **종료 예정일**: 2026-09-23 (2주 후)
- **성격**: 임시 실험 탭. 판정/EV/진입가 없음 — 순수 관찰용.

## 목적

당일 급하게 튀어오른 종목(+15%↑ & 거래량 20일평균 10배↑, KR)을 그날
그대로 기록해두고, 그 뒤 D+n 시점에 가격이 어떻게 됐는지만 계속 보여준다.
"그날 급등한 게 그 뒤 어떻게 됐나"를 눈으로 누적해서 보는 것 자체가
목적 — 조건·배지·score 없음(scanner.py의 다른 탭들과 달리 게이트/EV
검증 대상이 아님).

기존 "⋯실험" 드롭다운의 **⚡급등**(`data-mode="surge"`, `analyze_surge`
게이트 스캔 — 당일 +7%↑ & 거래량 4배↑ & 직전 조용함 조건, RS/거래량수축
가점 있는 판정형 탭)과는 **완전히 다른 기능**이다. 혼동 방지를 위해
별도 mode 키(`surge_observe`)를 썼고, 두 탭 모두 남겨둠(기존 ⚡급등 미변경).

## 구현

- **조회**: `_compute_surge_observe_today()`(app.py) — 이미 캐시된 일봉
  데이터(`_data_cache["data:all"/"data:kr"]`)에서만 계산, 새 fetch 없음.
  조건: 당일 종가 전일比 +15%↑ & 당일 거래량이 직전 20일 평균의 10배↑
  (`SURGE_OBSERVE_CHG_MIN_PCT`/`SURGE_OBSERVE_VOL_MULT_MIN`).
- **저장**: `_record_surge_observe_eod()` — `_warm_market()`의 KR EOD
  확정 분기(장마감 후 하루 1회, 기존 종가베팅/재점화 EOD 갱신과 같은
  타이밍)에서 그날 daykey로 1회 기록(멱등 — 이미 있으면 스킵).
  경로: `/data/surge_observe.json`(`_resolve_persistent_path`, 영구 볼륨).
  스키마: `{"YYYY-MM-DD": [{ticker, name, close, chg_pct, vol_mult, rs,
  atr_pct, ma200_pct}, ...]}` — sector는 정적 매핑이라 저장 안 하고
  조회 시점에 `_sector_of()`로 매번 다시 붙임(재계산 아님, 단순 조회).
- **과거 관찰 상태 계산**: `_surge_observe_history_row()` — 저장된 종목의
  관찰일 위치를 캐시된 df(`_calendar_ticker_df`)에서 찾아 D+n(그 날짜
  이후 실제 거래일 수), 현재가(`_calendar_current_price`), 관찰일 종가
  대비 변화%, 관찰일 이후 최고가 대비 현재 괴리%(고점 대비)를 계산 —
  전부 캐시 조회만, 새 fetch 없음.
- **API**: `GET /api/surge/observe` → `{today, today_date, history,
  banner, criteria}`.
- **UI**: 상단 탭 줄(종가베팅 오른쪽)에 "🔥 급등" 신설, 상단 고정 배너
  "관찰 전용 — 진입 근거 없음". "오늘 급등" + "과거 관찰"(한 줄씩:
  종목 · D+n · 당시종가→현재가(변화%) · 고점대비) 두 섹션.

## 종료 시 처리(2026-09-23 이후 검토)

- 계속 유지할 가치가 있으면: 임시 표기("2주 실험" 배지, 상단 배너 문구)
  제거하고 정식 탭으로 전환.
- 가치가 낮으면: 탭 제거(코드는 되돌리기 쉽게 이 커밋 하나로 묶여 있음),
  `surge_observe.json`은 삭제 또는 보관.
- 어느 쪽이든 이 문서에 결과를 추가 기록.
