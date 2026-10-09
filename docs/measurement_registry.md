# 측정 등록부

> 2026-10-09 신설(사용자 결정). "새 측정은 한 달에 하나" 제한을 폐지하고, 대신 **모든 측정을 여기에 한 줄씩** 남긴다.
> - **실행 전 "등록"**: 사전등록 문서가 커밋되면 행을 추가하고 판정 칸에 `등록(미실행)`.
> - **실행 후 "결과"**: 판정 칸을 채운다. **기각·미달·재현 불가도 기록한다**(지우지 않는다).
> - 사전등록 → 결과 본 뒤 조건 변경 금지(미달이어도 재실행 금지), 한 측정 안의 여러 가설은 Bonferroni — 그대로다(CLAUDE.md
>   "측정 운영 규칙").
> - 판정 표기: 채택 / 기각 / 철회(이전 채택을 뒤집음) / 측정만(채택 기준 없는 기술) / 회귀확인 / 진단 / 판단 불가 / 결과 기록 누락.
> - 2026-09-01 ~ 2026-10-09 행은 신설 시점에 docs·결과 파일에서 옮겨 적었다(문서 표기를 그대로 따름 — 애매한 건은 표 아래 메모).

| 날짜 | 측정(스크립트 `scripts/measurements/<이름>.py`) | 가설·측정 | 판정 | 문서 |
|---|---|---|---|---|
| 09-01 | 2026-09-01_confirm_entry_90cp_revalidation | 안C(돌파임박)/안C'(눌림목) 확인진입 EV 90cp 재검증 | 채택 → **철회**(09-04, 피벗진입 가정 무효) | docs/kr_us_strategy_map.md "우선순위5", docs/confirm_entry_lookahead_2026-09-04.md |
| 09-01 | 2026-09-01_depth_atr_gate_90cp_revalidation | 눌림폭 depth_atr 게이트 증분 EV 재검증 | **철회**(v5.132): 증분 0.018R < 기존 0.061R | docs/kr_us_strategy_map.md "우선순위1" |
| 09-01 | 2026-09-01_jongga_universe_v2_revalidation | 종가베팅 조합 A를 거래대금 유니버스 v2로 재측정 | **채택 유지**: 비용 차감 +0.80%, z 3.54, n 292 | docs/kr_jongga_betting_backtest.md "재측정 결과(2026-09-01)" |
| 09-01 | 2026-09-01_kr_multi_hit_90cp_revalidation | KR 돌파 계열 다중히트(🔱) 보너스 EV | **철회**(v5.134): +0.055R, z 1.32 | docs/kr_us_strategy_map.md "우선순위3" |
| 09-01 | 2026-09-01_kr_rsi_under50_90cp_revalidation | KR 돌파 계열 RSI<50 회피 | **철회**(v5.135): 반분 +0.12/−0.02R | docs/kr_us_strategy_map.md "우선순위4" |
| 09-01 | 2026-09-01_macro_regime_sector_performance | 매크로 레짐별 섹터 성과 예측력 | **기각**: 겹침률 KR 6.7%·US 20.8%(기준 50%) | docs/macro_regime_sector.md |
| 09-01 | 2026-09-01_macro_shortterm_shock_sector_reaction | 지표 급변 뒤 주간 섹터 반응 예측력 | **기각**: 겹침률 KR 29.2%·US 20.8% | docs/macro_shortterm_shock_reaction.md |
| 09-01 | 2026-09-01_reignition_confirm_close_vs_high | 재점화 확인진입 종가 vs 고가 기준 | **판단 불가**: z 0.40, 기존(고가) 유지 | docs/kr_theme_leader_reignition.md "후속 — Close vs High" |
| 09-01 | 2026-09-01_rs_gate_e_90cp_revalidation | RS 게이트 E 증분 EV | **철회**(v5.133): 증분 0.034R < 0.084R | docs/kr_us_strategy_map.md "우선순위2" |
| 09-02 | 2026-09-02_canslim_eps_growth_pullback_us | US 눌림목 EPS 성장(C·A) 필터 | **기각**: 지표 6개 전부 미달 | docs/canslim_eps_growth_pullback_us_investigation.md |
| 09-02 | 2026-09-02_post_entry_stall_exit_ev | 진입 후 정체 조기청산 vs 보유 | **기각**: 통과 0/27(최대 z 1.93) · 09-12 재검증 재현 불가 | docs/post_entry_stall_exit_ev.md, docs/stall_exit_bench_revalidation.md |
| 09-03 | 2026-09-03_long_box_breakout_frequency | 120·250봉 장기 박스돌파 신규 히트 수 | **측정만**: 120봉은 기존 창과 91~94% 중복 | docs/kr_us_strategy_map.md "250봉 장기 박스돌파 창" |
| 09-03 | 2026-09-03_super_filter_ev_90cp_revalidation | 슈퍼대장 필터 소속 EV | **철회**: −0.020R, z −1.57 · 09-12 재검증 유지 | docs/kr_us_strategy_map.md "우선순위6", docs/super_filter_bench_revalidation.md |
| 09-04 | 2026-09-04_boxbreak_basevol_diagnostic | base_vol50 구/신 정의로 확인 판정이 갈리나 | **진단**: flip 0건 | docs/kr_us_strategy_map.md ⑤ |
| 09-04 | 2026-09-04_confirm_entry_grid_search_5tabs | 확인조건 격자(거래량×종가위치) 5탭 | 채택 → **철회**(09-04, 피벗진입 가정) | docs/kr_us_strategy_map.md "확인조건 격자탐색" |
| 09-04 | 2026-09-04_kr_confirm_entry_all_tabs_90cp_checks | 5탭 KR 안C 결론 보강 확인 | 채택 보강 → **철회**(09-04) | docs/kr_us_strategy_map.md "후속 확인 ①②③④" |
| 09-04 | 2026-09-04_kr_confirm_entry_all_tabs_90cp_entry_buystop | 안D(피벗 buy-stop)·안C' 5탭 | **기각**: 5탭 미달 · 09-12 재검증 유지 | docs/confirm_entry_lookahead_2026-09-04.md e), docs/buystop_bench_revalidation.md |
| 09-04 | 2026-09-04_kr_confirm_entry_all_tabs_90cp_entry_close | 확인진입 진입가 = 확인일 종가, 5탭 | 피벗진입 EV **철회** · 돌파임박 KR 채택(0.157R z 2.37) → **철회**(09-11) | docs/kr_us_strategy_map.md ⑥, docs/confirm_entry_close_bench_revalidation.md |
| 09-04 | 2026-09-04_kr_confirm_entry_all_tabs_90cp | KR 5탭 확인진입(안C) EV | 채택 → **철회**(09-04) | docs/confirm_entry_lookahead_2026-09-04.md a) |
| 09-04 | 2026-09-04_long_box_ev_stage2 | 250봉 비중복 장기 박스돌파 안C EV | **기각**: 0.256R z 1.58 | docs/kr_us_strategy_map.md "250봉 장기 박스돌파 창" |
| 09-04 | 2026-09-04_zerovol_prevalence_check | 5탭 KR base_vol≤0 빈도 | **진단**: 0건 | docs/kr_us_strategy_map.md ⑤ |
| 09-07 | 2026-09-07_kr_us_breakout_boxbreak_post_pivot_consolidation_ev | 돌파일 종가의 피벗 대비 ATR 거리별 EV | **채택(표시 전용 경고)**: 0~0.5ATR z ≤ −2.75 · 안착 대기 기각 · 09-12 유지 | docs/kr_us_strategy_map.md, docs/display_only_bench_revalidation.md |
| 09-07 | 2026-09-07_kr_us_confirm_entry_stop_width_atr_multiple_ev | 손절폭 ATR 배수별 손절 도달률·EV | **채택(표시 전용 정보)**: 10조합 단조 · 09-12 유지 | docs/kr_us_strategy_map.md, docs/display_only_bench_revalidation.md |
| 09-08 | 2026-09-08_kr_imminent_pre_pivot_entry_ev | KR 돌파임박 피벗 아래 선진입 | **기각**: −0.054R, z −1.15 | docs/kr_us_strategy_map.md |
| 09-08 | 2026-09-08_us_pullback_atr_pct_bucket_ev | US 눌림목 ATR% 구간별 EV | **기각**: z 2.30이나 반분 미재현 | docs/kr_us_strategy_map.md |
| 09-08 | 2026-09-08_us_pullback_immediate_2nd_sort_candidates | US 눌림목 2차 정렬 후보 3개 | **기각**: 3후보 미달 | docs/kr_us_strategy_map.md |
| 09-10 | 2026-09-10_jongga_exit_timing | 종가베팅 익일 시가 vs 익일 종가 매도 | **결과 기록 누락** — 결과 JSON `judgment.passed=false`(차이·z·반분 미달), docs엔 사전등록만 | docs/kr_jongga_betting_backtest.md "사전등록: 매도 타이밍" |
| 09-11 | 2026-09-11_confirm_entry_close_bench_revalidation | 종가진입 5탭 벤치마크 룩어헤드 수정 재검증 | **철회**(돌파임박 KR): z 1.84/1.93 | docs/confirm_entry_close_bench_revalidation.md |
| 09-11 | 2026-09-11_imminent_score_rank_vs_return | 돌파임박 score 순위 vs 5일 수익률 | **기각**("무의미"): ρ −0.047 | docs/imminent_score_rank_vs_return.md |
| 09-12 | 2026-09-12_buystop_bench_revalidation | 안D buy-stop 벤치마크 수정 재검증 | **원판정 유지**(기각) | docs/buystop_bench_revalidation.md |
| 09-12 | 2026-09-12_display_only_bench_revalidation | 표시 전용 근거 2건 벤치마크 수정 재검증 | **원판정 유지** | docs/display_only_bench_revalidation.md |
| 09-12 | 2026-09-12_stall_exit_bench_revalidation | 정체 조기청산 벤치마크 수정 재검증 | **판정 없음(재현 불가)**: R0 게이트 실패 | docs/stall_exit_bench_revalidation.md |
| 09-12 | 2026-09-12_super_filter_bench_revalidation | 슈퍼대장 필터 벤치마크 수정 재검증 | **철회 유지**: z −0.77 | docs/super_filter_bench_revalidation.md |
| 09-13 | 2026-09-13_jongga_surge_gap_count | 급등일 군·대조군 표본 수, 재현 게이트 | **진단**: 131 / 155, 게이트 통과 | docs/jongga_surge_day_gap.md §1.3 |
| 09-13 | 2026-09-13_jongga_surge_gap_test | 종가베팅 급등일(+15%) 익일 갭 vs 대조 | **통과 — 기록만**(사전등록 §1.5, 조건 변경 없음): z 2.26 | docs/jongga_surge_day_gap.md §2 |
| 09-13 | 2026-09-13_stage1to2_setup | Stage 1→2 셋업 20일 수익률 vs 눌림목 | **기각**: MWU z −3.08 | docs/stage1to2_base_setup.md §2 |
| 09-14 | 2026-09-14_pullback_quality_axes_b2 | 누락 3항목 판별력 | **기각**: 0/3 | docs/pullback_quality_axes_b2.md §2 |
| 09-14 | 2026-09-14_pullback_quality_axes | 눌림목 score 항목별 판별력 | **기각**: 0/14 | docs/pullback_quality_axes.md §2 |
| 09-18 | 2026-09-18_fetch_window_730_vs_1900_regression | KR fetch 730→1900일 5탭 회귀 | **회귀확인**: 차이 0건 | docs/kr_us_strategy_map.md "KR 창 730→1900일" |
| 10-06 | 2026-10-06_abc_display_only_check | v5.331 ABC 표시 전용 변경 전후 등급 | **회귀확인**: 2,455종목 차이 0건 | CLAUDE.md ABC 항목 |
| 10-09 | 2026-10-09_ma99_breakout_retest | 바닥형 MA99 돌파 뒤 되돌림 지지 진입 EV | **기각**: A EV −0.053R, z 2.58(판정식 1·3 미달) | docs/ma99_breakout_retest.md §2 |
| 10-09 | 2026-10-09_lowpoint_departure_rest_entry | 저점 주봉 히트 출발 다음 날 진입 EV(H1) · 출발 크기 상·하(H2) | **등록(미실행)** — 해석 확인 대기 | docs/lowpoint_departure_rest_entry.md |

**메모(옮겨 적을 때 애매했던 건)**
- 09-10 종가베팅 매도 타이밍: docs 사전등록 절은 스크립트를 `2026-09-09_jongga_exit_timing.py`로 적었고 결과 절이 없다. 판정은
  결과 JSON(`2026-09-10_jongga_exit_timing.stage2.json`)에만 남아 있다 — docs 결과 절 보충이 필요하다.
- 09-13 급등일 갭: 기준은 통과했지만 사전등록이 "기록만, 조건 변경 없음"이라 채택 분류가 아니다.
- 09-01 재점화 종가/고가: 문서 표기 "판단 불가(임의 채택 안 함)" 그대로.
- 09-11 score 순위: 문서 표기 "무의미" — 방향 가설 없는 측정을 기각으로 분류했다.
- 09-14 두 측정·09-12 정체 조기청산: 문서가 스크립트 파일명을 직접 인용하지 않거나 자리표시자로 적었다(내용·docstring으로 연결).
