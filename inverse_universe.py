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
# 미국 인버스 ETF (1x만 — 곱버스 2x/3x는 변동성 극심 + 데이터 불안정으로 제외)
US_INVERSE = {
    "PSQ":  {"name": "나스닥100 인버스 1x", "leverage": 1, "underlying": "나스닥100"},
    "SH":   {"name": "S&P500 인버스 1x", "leverage": 1, "underlying": "S&P500"},
    "DOG":  {"name": "다우 인버스 1x", "leverage": 1, "underlying": "다우"},
    "RWM":  {"name": "러셀2000 인버스 1x", "leverage": 1, "underlying": "러셀2000"},
    "VIXY": {"name": "VIX 단기선물", "leverage": 1, "underlying": "VIX"},
}

# 한국 인버스 ETF (1x만 — 곱버스 2x는 네이버 데이터가 거꾸로 들어와 제외)
KR_INVERSE = {
    "114800.KS": {"name": "KODEX 인버스", "leverage": 1, "underlying": "코스피200"},
    "251340.KS": {"name": "KODEX 코스닥150선물인버스", "leverage": 1, "underlying": "코스닥150"},
    "123310.KS": {"name": "KODEX 인버스 (구)", "leverage": 1, "underlying": "코스피200"},
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
