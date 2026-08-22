"""
test_toss.py — toss_client.py 수동 확인용 스크립트.

TOSS_CLIENT_ID / TOSS_CLIENT_SECRET을 .env에 설정한 뒤:
    python test_toss.py
로 실행하면 보유 계좌, 보유종목, 평가금액을 콘솔에 출력한다.
"""

import sys

from toss_client import TossApiError, TossAuthError, TossClient


def fmt_amount(amount_str, currency):
    if amount_str is None:
        return "-"
    try:
        value = float(amount_str)
    except (TypeError, ValueError):
        return str(amount_str)
    symbol = "₩" if currency == "KRW" else "$"
    return f"{symbol}{value:,.2f}" if currency != "KRW" else f"{symbol}{value:,.0f}"


def fmt_rate(rate_str):
    if rate_str is None:
        return "-"
    try:
        return f"{float(rate_str) * 100:+.2f}%"
    except (TypeError, ValueError):
        return str(rate_str)


def main():
    try:
        client = TossClient()
    except TossAuthError as e:
        print(f"❌ 클라이언트 초기화 실패: {e.message}", file=sys.stderr)
        sys.exit(1)

    try:
        accounts = client.get_accounts()
    except TossApiError as e:
        print(f"❌ 계좌 조회 실패: [{e.code}] {e.message}", file=sys.stderr)
        sys.exit(1)

    if not accounts:
        print("조회 가능한 종합매매 계좌가 없습니다.")
        sys.exit(0)

    account = accounts[0]
    print(f"계좌: {account['accountNo']} (accountSeq={account['accountSeq']}, {account['accountType']})")
    if len(accounts) > 1:
        print(f"⚠️ 계좌가 {len(accounts)}개입니다 — 첫 번째 계좌만 조회합니다.")
    print()

    try:
        holdings = client.get_holdings(account_seq=account["accountSeq"])
    except TossApiError as e:
        print(f"❌ 보유종목 조회 실패: [{e.code}] {e.message}", file=sys.stderr)
        sys.exit(1)

    items = holdings.get("items", [])
    if not items:
        print("보유 중인 종목이 없습니다.")
        sys.exit(0)

    print(f"{'종목':<20} {'수량':>10} {'현재가':>14} {'평가금액':>16} {'손익':>16} {'수익률':>10}")
    print("-" * 92)
    for item in items:
        name = f"{item['name']} ({item['symbol']})"
        currency = item["currency"]
        line = (
            f"{name:<20} {item['quantity']:>10} "
            f"{fmt_amount(item['lastPrice'], currency):>14} "
            f"{fmt_amount(item['marketValue']['amount'], currency):>16} "
            f"{fmt_amount(item['profitLoss']['amount'], currency):>16} "
            f"{fmt_rate(item['profitLoss']['rate']):>10}"
        )
        print(line)

    print("-" * 92)
    mv = holdings["marketValue"]["amount"]
    pl = holdings["profitLoss"]
    print(f"평가금액 합계: KRW {fmt_amount(mv['krw'], 'KRW')} / USD {fmt_amount(mv['usd'], 'USD')}")
    print(f"손익 합계:     KRW {fmt_amount(pl['amount']['krw'], 'KRW')} / USD {fmt_amount(pl['amount']['usd'], 'USD')}")
    print(f"전체 수익률:   {fmt_rate(pl['rate'])}")


if __name__ == "__main__":
    main()
