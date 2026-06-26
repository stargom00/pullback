"""
인버스 ETF 유니버스 — 지수 하락에 베팅하는 종목.
일반 종목과 로직이 반대: 인버스가 강세 = 지수가 약세 = 시장 하락 국면.

용도:
  (1) 시장 국면 확인 — 인버스가 다수 강세면 하락장 확정 신호
  (2) 매매 신호 — 하락장에서 짧게 칠 인버스 후보

주의: 곱버스(2x/3x)는 변동성 극심 + 일일 리밸런싱으로 장기보유 시 가치 침식.
      단기 매매만, 비중 작게. 미너비니式은 본래 현금이 우선 — 인버스는 보조.
"""

# 미국 인버스 ETF (지수 하락 베팅)
# 미국 인버스 ETF
US_INVERSE = {
    "PSQ":  {"name": "나스닥100 인버스 1x", "leverage": 1, "underlying": "나스닥100"},
    "QID":  {"name": "나스닥100 인버스 2x", "leverage": 2, "underlying": "나스닥100", "derive_from": "PSQ"},
    "SQQQ": {"name": "나스닥100 인버스 3x", "leverage": 3, "underlying": "나스닥100", "derive_from": "PSQ"},
    "SH":   {"name": "S&P500 인버스 1x", "leverage": 1, "underlying": "S&P500"},
    "SDS":  {"name": "S&P500 인버스 2x", "leverage": 2, "underlying": "S&P500", "derive_from": "SH"},
    "SPXU": {"name": "S&P500 인버스 3x", "leverage": 3, "underlying": "S&P500", "derive_from": "SH"},
    "DOG":  {"name": "다우 인버스 1x", "leverage": 1, "underlying": "다우"},
    "RWM":  {"name": "러셀2000 인버스 1x", "leverage": 1, "underlying": "러셀2000"},
    "SOXS": {"name": "반도체 인버스 3x", "leverage": 3, "underlying": "반도체"},
    "VIXY": {"name": "VIX 단기선물", "leverage": 1, "underlying": "VIX"},
}

# 한국 인버스 ETF (국장 지수 하락 베팅 상품)
# 곱버스(2x)는 네이버 일봉이 거꾸로 들어와, 같은 기초지수 1x ETF에서 등락을 역산.
KR_INVERSE = {
    "114800.KS": {"name": "KODEX 인버스", "leverage": 1, "underlying": "코스피200"},
    "252670.KS": {"name": "KODEX 200선물인버스2X (곱버스)", "leverage": 2, "underlying": "코스피200", "derive_from": "114800.KS"},
    "251340.KS": {"name": "KODEX 코스닥150선물인버스", "leverage": 1, "underlying": "코스닥150"},
    "291630.KS": {"name": "KODEX 코스닥150선물인버스2X (곱버스)", "leverage": 2, "underlying": "코스닥150", "derive_from": "251340.KS"},
    "252710.KS": {"name": "TIGER 200선물인버스2X (곱버스)", "leverage": 2, "underlying": "코스피200", "derive_from": "114800.KS"},
}


def inverse_universe(market: str = "all") -> dict:
    """인버스 ETF 유니버스 반환. market: 'us'|'kr'|'all'.
    반환: {ticker: {name, leverage, underlying, market}}"""
    out = {}
    if market in ("us", "all"):
        for t, meta in US_INVERSE.items():
            out[t] = {**meta, "market": "US"}
    if market in ("kr", "all"):
        for t, meta in KR_INVERSE.items():
            out[t] = {**meta, "market": "KR"}
    return out
