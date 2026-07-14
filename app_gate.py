"""
app.py 교체 블록 (v4.57)

교체 대상:
  1. def _index_regime(code)          → 통째로 교체
  2. @app.get("/api/market/gate")     → 통째로 교체
  3. _indices_impl() 안의 gather 블록 → S&P500 추가 (아래 3번 참조)
"""

# ══════════════════════════════════════════════════════════════
# 1) _index_regime — 통째로 교체
# ══════════════════════════════════════════════════════════════

# 지수 코드 → (표시명, 거래량 소스 티커).
# ⚠️ 거래량 소스가 지수 자체와 다른 이유:
#    yfinance의 지수 Volume(^GSPC, ^IXIC)은 소스에 따라 0이거나 결측이다.
#    분산일 = "하락 + 거래량 증가"이므로 Volume이 0이면 판정 자체가 불가능.
#    그런데 0을 그대로 쓰면 (vol > vol.shift(1))이 전부 False → 분산일 0개
#    → "건강한 시장"이라는 정반대 신호가 나온다.
#    그래서 지수 Volume이 무효면 대표 ETF(SPY/QQQ) 거래량으로 폴백한다.
#    ETF 거래량은 지수 거래량의 프록시일 뿐 동일하지 않다 — 실무 표준이지만
#    오닐 원본(NYSE/나스닥 전체 거래량)과는 다르다는 점을 명시해 둔다.
#    폴백도 실패하면 dist_days=None → 게이트가 '판정 불가'로 처리.
INDEX_SPEC = {
    "KOSPI":  {"label": "코스피",  "vol_proxy": None},   # 네이버가 지수 거래량 제공
    "KOSDAQ": {"label": "코스닥",  "vol_proxy": None},
    "^IXIC":  {"label": "나스닥",  "vol_proxy": "QQQ"},
    "^GSPC":  {"label": "S&P500", "vol_proxy": "SPY"},
}


def _volume_valid(vol) -> bool:
    """지수 거래량이 분산일 판정에 쓸 만한가.
    전부 0/NaN이거나 결측이 30%를 넘으면 무효."""
    try:
        if vol is None or len(vol) < 30:
            return False
        tail = vol.iloc[-30:]
        if float(tail.fillna(0).sum()) <= 0:
            return False
        if int(tail.isna().sum()) > 9:
            return False
        return True
    except Exception:
        return False


def _fetch_proxy_volume(ticker: str, n: int):
    """대표 ETF(SPY/QQQ)의 거래량 시리즈. 지수 Volume이 무효일 때 폴백."""
    try:
        df = yf.Ticker(ticker).history(period="6mo", interval="1d", auto_adjust=False)
        if df is None or df.empty or "Volume" not in df:
            return None
        v = df["Volume"].dropna()
        return v if len(v) >= n else None
    except Exception:
        return None


def _index_regime(code: str) -> dict | None:
    """지수 일봉으로 시장 레짐 판정 (오닐/미너비니 M factor). v4.57 전면 개편.

    code: 'KOSPI' | 'KOSDAQ' | '^IXIC' | '^GSPC'

    [v4.57 근본수정] 세 가지 버그를 고침:
      ① 분산일 카운트가 순수 25일 롤링이라 FTD 리셋도 5% 만료도 없었음
         → "늘어나기만 하고 안 빠짐". 조정 끝나고 반등해도 한 달간 갇힘.
         → scanner.dist_count()로 분리, 두 제거 규칙 적용.
      ② gate_suggest가 `dist_days >= 6` early return을 맨 위에 둬서 FTD
         분기가 도달 불가능한 죽은 코드였음. FTD는 조정 뒤에 나오므로
         분산일이 6+인 게 정상인데, 그걸 먼저 걸러버림.
         → scanner.gate_suggest()에서 FTD를 먼저 평가하도록 순서 뒤집음.
      ③ 지수 Volume이 0이면 분산일 0개 → confirmed. 정반대 신호.
         → Volume 유효성 검증 + ETF 폴백 + 실패 시 None(판정 불가) 명시.

    반환에 추가된 필드:
      dist_raw    : 제거 규칙 적용 전 순수 25일 카운트 (비교/진단용)
      dist_expired: 5% 룰로 만료된 개수
      dist_pre_ftd: FTD 이전이라 제외된 개수
      vol_source  : 거래량 출처 ('index' | 'SPY' | 'QQQ' | 'none')
      recovered   : 조정 회복 완료 여부
    """
    try:
        spec = INDEX_SPEC.get(code, {})
        close, vol = None, None

        if code in ("^IXIC", "^GSPC"):
            df = yf.Ticker(code).history(period="6mo", interval="1d", auto_adjust=False)
            if df is None or df.empty:
                return None
            close = df["Close"].dropna()
            vol = df["Volume"] if "Volume" in df.columns else None
        else:
            hist = naver_kr.fetch_index_history(code, days=160)
            if hist is None or hist.empty:
                return None
            close = hist["Close"]
            vol = hist["Volume"] if "Volume" in hist.columns else None

        if close is None or len(close) < 60:
            return None

        # ── 거래량 소스 결정 (검증 → 폴백 → 포기) ──
        vol_source = "none"
        if _volume_valid(vol):
            vol_source = "index"
            # 인덱스 정렬 (close와 길이/인덱스가 맞아야 함)
            vol = vol.reindex(close.index)
        else:
            proxy = spec.get("vol_proxy")
            if proxy:
                pv = _fetch_proxy_volume(proxy, len(close))
                if pv is not None and _volume_valid(pv):
                    # ETF 거래량을 지수 close 인덱스에 맞춰 정렬.
                    # 거래일이 같으므로 날짜 기준 정렬이면 대체로 일치한다.
                    aligned = pv.reindex(close.index)
                    if _volume_valid(aligned):
                        vol = aligned
                        vol_source = proxy
                    else:
                        vol = None
                else:
                    vol = None
            else:
                vol = None

        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean()
        cur = float(close.iloc[-1])
        m20 = float(ma20.iloc[-1])
        m60 = float(ma60.iloc[-1])
        m20_prev = float(ma20.iloc[-6])
        rising20 = m20 > m20_prev
        above60 = cur > m60

        # ── FTD 상태 머신 ──
        if vol is not None:
            fs = scanner_mod.ftd_state(close, vol)
        else:
            fs = {"in_correction": False, "rally_day": 0, "ftd": False,
                  "ftd_days_ago": None, "ftd_idx_back": None, "rally_low": None,
                  "peak_before": None, "drawdown_pct": 0.0, "recovered": False}

        # ── 분산일 (FTD 리셋 + 5% 만료 적용) ──
        dc = scanner_mod.dist_count(close, vol, fs)

        # ── 게이트 제안 ──
        gate_sug, gate_why = scanner_mod.gate_suggest(dc, fs, above60)

        # ── 배너 표시용 레짐 (게이트와 별개, 3색 라벨) ──
        d = dc.get("days")
        if gate_sug == "correction":
            regime, txt = "bad", "비우호 (신규진입 자제)"
        elif gate_sug == "pressure":
            regime, txt = "neutral", "주의 (선별 진입)"
        else:
            regime, txt = "good", "우호 (진입 환경 양호)"
        txt += f" · {gate_why}"

        return {
            "regime": regime, "regime_txt": txt,
            "above_ma20": cur > m20, "above_ma60": above60,
            "ma20_rising": rising20,
            # 분산일 — days가 None이면 '판정 불가' (0 아님!)
            "dist_days": d,
            "dist_raw": dc.get("raw"),
            "dist_expired": dc.get("expired"),
            "dist_pre_ftd": dc.get("pre_ftd"),
            "vol_source": vol_source,
            # FTD
            "ftd": bool(fs.get("ftd")),
            "ftd_days_ago": fs.get("ftd_days_ago"),
            "rally_day": fs.get("rally_day", 0),
            "in_correction": fs.get("in_correction", False),
            "recovered": fs.get("recovered", False),
            "drawdown_pct": fs.get("drawdown_pct", 0.0),
            # 게이트
            "gate_suggest": gate_sug, "gate_why": gate_why,
        }
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════
# 2) /api/market/gate — 통째로 교체 (4개 지수)
# ══════════════════════════════════════════════════════════════

# 게이트 강도 순서. 여러 지수 중 '가장 나쁜' 쪽을 채택할 때 씀.
_GATE_RANK = {"confirmed": 0, "pressure": 1, "correction": 2}

# 게이트별 신규 진입 허용 오픈 리스크(R).
# ⚠️ 이 숫자는 새로 만든 게 아니라 v4.47부터 R 설정/GUIDE에 이미 정의된 값이다.
#    🟢확인된 상승=3R / 🟡조정 압박=1.5R / 🔴조정=신규 0.
#    (근거 없는 '노출 %'를 새로 지어내지 않는다 — 이미 있는 규칙을 그대로 쓴다.
#     실제 상한은 rsettings의 max_open_r이며, 아래는 게이트별 비율 적용값)
_GATE_R = {"confirmed": 1.0, "pressure": 0.5, "correction": 0.0}   # max_open_r 대비 배수


def _worst_gate(gates: list[str]) -> str:
    """여러 지수 게이트 중 가장 보수적인 것. 판정 불가(None)는 제외."""
    valid = [g for g in gates if g]
    if not valid:
        return "correction"     # 전부 판정 불가 → 보수적으로
    return max(valid, key=lambda g: _GATE_RANK.get(g, 2))


@app.get("/api/market/gate")
async def market_gate():
    """시장 게이트 자동 제안 (v4.57) — 4개 지수 전부.

    기존엔 KOSPI 하나만 봤다. 미국 종목 알림을 낼 때 참고할 게이트가 없었고,
    코스닥만 무너져도 코스피 게이트가 confirmed면 진입이 열렸다.

    반환:
      indices     : 지수별 상세 (게이트/분산일/FTD/거래량 소스)
      gate_kr     : KOSPI/KOSDAQ 중 나쁜 쪽 (한국 종목 진입 판단)
      gate_us     : ^GSPC/^IXIC 중 나쁜 쪽 (미국 종목 진입 판단)
      suggest     : 전체 중 가장 나쁜 것 (봇 알림 호환 — 기존 필드 유지)
      why         : suggest의 근거
      max_open_r  : 게이트별 오픈 리스크 상한 (R 설정의 max_open_r × 배수)
    """
    loop = asyncio.get_event_loop()
    codes = ["KOSPI", "KOSDAQ", "^GSPC", "^IXIC"]
    regs = await asyncio.gather(*[
        loop.run_in_executor(_executor, _index_regime, code) for code in codes
    ], return_exceptions=True)

    out_idx = {}
    for code, reg in zip(codes, regs):
        if isinstance(reg, BaseException) or not reg:
            out_idx[code] = None
            continue
        out_idx[code] = {
            "label": INDEX_SPEC[code]["label"],
            "gate": reg.get("gate_suggest"),
            "why": reg.get("gate_why"),
            "dist_days": reg.get("dist_days"),
            "dist_raw": reg.get("dist_raw"),
            "dist_expired": reg.get("dist_expired"),
            "dist_pre_ftd": reg.get("dist_pre_ftd"),
            "vol_source": reg.get("vol_source"),
            "ftd": reg.get("ftd"),
            "ftd_days_ago": reg.get("ftd_days_ago"),
            "rally_day": reg.get("rally_day"),
            "in_correction": reg.get("in_correction"),
            "recovered": reg.get("recovered"),
            "drawdown_pct": reg.get("drawdown_pct"),
            "above_ma60": reg.get("above_ma60"),
        }

    if not any(out_idx.values()):
        return JSONResponse({"ok": False, "error": "전 지수 조회 실패"}, status_code=503)

    def g(code):
        v = out_idx.get(code)
        return v.get("gate") if v else None

    gate_kr = _worst_gate([g("KOSPI"), g("KOSDAQ")])
    gate_us = _worst_gate([g("^GSPC"), g("^IXIC")])
    suggest = _worst_gate([gate_kr, gate_us])

    # suggest의 근거 = 그 게이트를 만든 지수의 why
    why = ""
    for code in codes:
        v = out_idx.get(code)
        if v and v.get("gate") == suggest:
            why = f"{v['label']}: {v['why']}"
            break

    # 현재 저장된 게이트 (수동 설정값)
    cur = dict(RSETTINGS_DEFAULT)
    if os.path.exists(RSETTINGS_PATH):
        try:
            with open(RSETTINGS_PATH, encoding="utf-8") as f:
                saved = _json.load(f)
                if isinstance(saved, dict):
                    cur.update(saved)
        except (ValueError, OSError):
            pass

    base_r = float(cur.get("max_open_r", 3.0))
    ftd_any = any(v.get("ftd") for v in out_idx.values() if v)

    return JSONResponse(_clean_nan({
        "ok": True,
        "indices": out_idx,
        "gate_kr": gate_kr,
        "gate_us": gate_us,
        "max_open_r_kr": round(base_r * _GATE_R.get(gate_kr, 0.0), 2),
        "max_open_r_us": round(base_r * _GATE_R.get(gate_us, 0.0), 2),
        # ── 기존 봇 호환 필드 (v2.2 check_market_gate가 읽음) ──
        "suggest": suggest,
        "why": why,
        "current": cur.get("gate"),
        "ftd": ftd_any,
    }))


# ══════════════════════════════════════════════════════════════
# 3) _indices_impl() — S&P500 추가
#
# 기존 gather 블록에서 아래 두 줄을 추가/수정:
#   (a) gather 인자에 S&P 지수 fetch + regime 추가
#   (b) 언팩 및 병합
#
# 아래는 수정된 전체 gather 부분. _indices_impl() 안의 해당 구간만 교체.
# ══════════════════════════════════════════════════════════════
"""
    loop = asyncio.get_event_loop()
    results = await asyncio.gather(
        loop.run_in_executor(_executor, _fetch_nasdaq),
        loop.run_in_executor(_executor, naver_kr.fetch_index, "KOSPI"),
        loop.run_in_executor(_executor, naver_kr.fetch_index, "KOSDAQ"),
        loop.run_in_executor(_executor, _index_regime, "KOSPI"),
        loop.run_in_executor(_executor, _index_regime, "KOSDAQ"),
        loop.run_in_executor(_executor, _index_regime, "^IXIC"),
        loop.run_in_executor(_executor, _fetch_yf_index, "^N225", "닛케이"),
        loop.run_in_executor(_executor, _fetch_yf_index, "BTC-USD", "비트코인"),
        # v4.57: S&P500 추가 (게이트 4개 지수 확장)
        loop.run_in_executor(_executor, _fetch_yf_index, "^GSPC", "S&P500"),
        loop.run_in_executor(_executor, _index_regime, "^GSPC"),
        return_exceptions=True,
    )
    nasdaq, kospi, kosdaq, r_kospi, r_kosdaq, r_nasdaq, nikkei, btc, sp500, r_sp500 = [
        (None if isinstance(x, BaseException) else x) for x in results
    ]
    if isinstance(kospi, dict) and isinstance(r_kospi, dict): kospi.update(r_kospi)
    if isinstance(kosdaq, dict) and isinstance(r_kosdaq, dict): kosdaq.update(r_kosdaq)
    if isinstance(nasdaq, dict) and isinstance(r_nasdaq, dict): nasdaq.update(r_nasdaq)
    if isinstance(sp500, dict) and isinstance(r_sp500, dict): sp500.update(r_sp500)
    # 순서: 코스피, 코스닥, S&P500, 나스닥, 닛케이, 비트코인
    data = {"indices": [x for x in (kospi, kosdaq, sp500, nasdaq, nikkei, btc)
                        if isinstance(x, dict)]}
"""
