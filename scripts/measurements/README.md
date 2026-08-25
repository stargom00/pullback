# 측정 스크립트 (v5.68~)

## 왜 여기 있는가

이전엔 investigation 스크립트를 실행하고 결과만 `docs/*.md`에 적은 뒤
스크립트 자체는 지웠다(1회성이라 커밋 안 함 관례). 2026-08-14에 그
관례가 사고를 냈다: 눌림목 EV 재측정치(0.286R)가 기존 기록(0.172R)과
66% 차이가 났는데, 원본 스크립트(all_tabs_common_yardstick_investigation.md의
"Script A")가 남아있지 않아 뭐가 달랐는지 끝내 특정하지 못했다. 오늘
기준으로 Script A 계열 조사가 근거가 된 결정이 최소 5개(패턴탭
"검증실패" 라벨, 신호등 U/D 항목 제거, tightening/vol_dry 채점 제거,
rs_min 85→80, 슈퍼대장 즉시진입 ATR×2) — 전부 같은 이유로 재현 불가
상태다. 자세한 경위: `docs/pullback_stop_width_and_entry_timing.md`
"기준선 불일치 조사" 절.

## 규칙

1. **결정 근거가 되는 측정은 스크립트를 커밋한다.** "결정 근거"란: 이
   측정 결과가 CONFIG 값을 바꾸거나, 배지/필터를 넣거나 빼거나, 탭에
   "검증실패" 같은 라벨을 붙이는 데 쓰였다는 뜻. 단순 호기심에 돌려보고
   버리는 탐색적 스크립트까지 전부 남길 필요는 없다 — 결과가 코드/UI/
   문서의 확정적 진술로 남는 경우만.
2. **파일명 = `YYYY-MM-DD_무엇을쟀는지.py`.** 날짜는 실행일(측정 결과가
   그 시점의 유니버스·시장 상태를 반영한다는 걸 명시하기 위함).
3. **공통 로직은 `harness.py`에서 가져다 쓴다.** RS/RS모멘텀 계산,
   2R 레이스, 체크포인트 생성, 프로덕션 사후 필터(저유동성 등) 재현을
   스크립트마다 새로 구현하지 않는다 — 이번 사고의 핵심 원인이 "제각각
   구현"이었다. 하네스에 없는 새 로직이 필요하면 하네스를 확장하거나,
   왜 이 측정만 다르게 재는지 스크립트에 주석으로 남긴다.
4. **`docs/*.md`의 측정 결과 문단은 이 폴더의 파일명을 인용한다.**
   "근거: `scripts/measurements/2026-08-14_....py`" 형태로. 스크립트가
   없는 기존 측정(아래 목록)은 문서에 "재현 불가"로 명시한다.
5. **결과 데이터(.json 등)는 커밋하지 않는다** — 코드만. 재실행하면
   그 시점 데이터로 재현되는 게 요점이라, 결과 스냅샷을 박제해두면
   오히려 "이게 최신"이라는 착각을 준다. 결과 숫자는 `docs/*.md`에
   적는다.
6. **대조군(시점매칭)은 반드시 대상 탭과 동일한 유동성 컷을 통과한
   종목에서 추출한다 — 완전무작위 대조군(유동성 필터 미적용) 금지.**
   2026-08-25 8/17 캠페인에서 실측된 왜곡 두 건이 근거:
   - **추세전환**: 유동성 필터 없는 코호트로 잰 EV 0.362(Script A)가
     필터 적용 후 0.211로 확인됨(46% 과대) — 표본 자체가 저유동성
     종목으로 부풀려져 있었음(`2026-08-25_turnaround_ev_liquidity_filtered_control.py`).
   - **Stage2**: 완전무작위 대조군을 쓰면 하락위험 열위가 +4.9pp인데,
     같은 유동성 컷을 통과한 대조군으로 바꾸면 +0.7pp(사실상 무차이)로
     떨어짐 — "검증실패" 판정의 절반이 대조군 자체가 저유동성(변동성
     크고 예측 어려운) 종목을 포함해서 생긴 착시였음
     (`2026-08-25_stage2_liquidity_matched_control.py`,
     `2026-08-25_stage2_liquidity_filter_touch_rate.py` 비교).

   즉 "실제 히트"는 유동성 필터를 통과했는데 "대조군"은 안 걸렀다면,
   두 그룹이 애초에 다른 모집단이라 비교 자체가 왜곡된다 — RS/템플릿
   같은 탭 고유 필터까지 대조군에 맞출 필요는 없지만(그러면 대조군이
   히트와 거의 같아져 비교 의미가 없어짐), **유동성만큼은 필수로
   매칭**한다.
7. **상위/하위 반분·사분위 비교로 필드를 채택할 땐 격차·단조성뿐 아니라
   통계적 유의성도 확인한다.** 2026-08-25 기관/외국인 수급 캠페인에서
   `inst_20d`가 사전 기준(격차 +0.05R 이상 + 4분위 대체로 단조)을
   전부 통과하고도, 표준오차 기반 z검정에선 z≈0.98(양측 95% 기준
   미달)로 표집 잡음과 구분되지 않았다 — 결과가 -1R/0R/+2R 세 값뿐인
   이산분포라 분산이 커서(그룹당 표준오차 ~0.08R) 격차·단조성만으론
   "우연"을 못 거르는 경우가 실제로 있음을 확인
   (`2026-08-25_institutional_flow_pullback_ev.py::ev_gap_zscore`,
   `docs/institutional_flow_pullback_ev.md` "재검토" 절). 표본을
   나눠서 EV를 비교하는 새 측정은 이 함수(또는 동등한 z검정)를 재사용할 것.

## 기존 docs/*.md 측정 스크립트 존재 여부 (2026-08-14 감사)

| 문서 | 인용된 스크립트 | 존재 여부 |
|---|---|---|
| `pullback_stop_width_and_entry_timing.md` | `2026-08-14_pullback_stop_width_and_entry_timing.py` | ✅ 여기 있음(v5.68부터) |
| `all_tabs_common_yardstick_investigation.md` | Script A~F, `entrysignal_before_after.py`, `item_pass_rates_all5.py`, `super_immediate_entry.py`, `super_pullback_prefilter_check.py`, `super_status_atr2.py`, `surge_accum_followup.py` | ❌ 전부 재현 불가 (Script A의 추세전환 부분·Script B/E의 Stage2 부분은 2026-08-25 재현 가능한 후속 스크립트로 대체 측정됨 — 아래 참고, 원본 자체는 여전히 없음) |
| `imminent_stop_entry_investigation.md` | (파일명 없이 인라인 서술) | ❌ 재현 불가 |
| `leader_to_pullback_watch.md` | `leader_check_cost.py`, `leader_to_pullback_dist.py` | ❌ 재현 불가 |
| `rs_definition_and_slope_investigation.md` | (파일명 없이 인라인 서술, `test_trace_parity.py` 등은 별개로 실존) | ❌ 재현 불가 |
| `ud_volume_ratio_investigation.md` | (파일명 없이 인라인 서술) | ❌ 재현 불가 |
| `abc_doc_style_tab_investigation.md` | (파일명 없이 인라인 서술) | ❌ 재현 불가 |

각 문서 상단에 재현 불가 노트를 추가하는 작업은 진행 중 —
`docs/pullback_stop_width_and_entry_timing.md`의 "기준선 불일치 조사"
절과 이 표를 참고해 순차 반영한다.
