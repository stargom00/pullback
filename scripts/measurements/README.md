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

## 기존 docs/*.md 측정 스크립트 존재 여부 (2026-08-14 감사)

| 문서 | 인용된 스크립트 | 존재 여부 |
|---|---|---|
| `pullback_stop_width_and_entry_timing.md` | `2026-08-14_pullback_stop_width_and_entry_timing.py` | ✅ 여기 있음(v5.68부터) |
| `all_tabs_common_yardstick_investigation.md` | Script A~F, `entrysignal_before_after.py`, `item_pass_rates_all5.py`, `super_immediate_entry.py`, `super_pullback_prefilter_check.py`, `super_status_atr2.py`, `surge_accum_followup.py` | ❌ 전부 재현 불가 |
| `imminent_stop_entry_investigation.md` | (파일명 없이 인라인 서술) | ❌ 재현 불가 |
| `leader_to_pullback_watch.md` | `leader_check_cost.py`, `leader_to_pullback_dist.py` | ❌ 재현 불가 |
| `rs_definition_and_slope_investigation.md` | (파일명 없이 인라인 서술, `test_trace_parity.py` 등은 별개로 실존) | ❌ 재현 불가 |
| `ud_volume_ratio_investigation.md` | (파일명 없이 인라인 서술) | ❌ 재현 불가 |
| `abc_doc_style_tab_investigation.md` | (파일명 없이 인라인 서술) | ❌ 재현 불가 |

각 문서 상단에 재현 불가 노트를 추가하는 작업은 진행 중 —
`docs/pullback_stop_width_and_entry_timing.md`의 "기준선 불일치 조사"
절과 이 표를 참고해 순차 반영한다.
