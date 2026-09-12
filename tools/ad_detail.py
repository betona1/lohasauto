"""
대용량 보고서로 **차원별 광고비**를 쌓고 본다 —
검색어 · 소재(상품) · 비즈채널 · PC/모바일 · 시간대 · LCP.

    python -X utf8 tools/ad_detail.py --collect            # 어제·그제
    python -X utf8 tools/ad_detail.py --collect --days 7
    python -X utf8 tools/ad_detail.py                      # 쌓인 것 보기
    python -X utf8 tools/ad_detail.py --dim query --top 30

당일치는 보고서가 안 나온다(`20007 준비중`). 오늘 광고비는
`tools/ad_spend.py` 가 `/stats` 로 본다.
"""
import argparse
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from app import db                                      # noqa: E402
from app.lohas import ad_account, ad_detail as ad       # noqa: E402

DIMS = [("device", "PC / 모바일"), ("channel_id", "비즈채널"),
        ("query", "검색어"), ("ad_id", "소재(상품)"),
        ("lcp_code", "LCP"), ("hour", "시간대")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--customer", default="")
    ap.add_argument("--dim", default="")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    db.init_db()
    cu = args.customer or (ad_account.main_account() or {}).get("customer_id")
    if not cu:
        print("!! 메인 광고계정이 없습니다"); return 1
    print(f"광고계정 {cu}")

    if args.collect:
        n = ad.collect_days(cu, days=args.days)
        print(f"=> {n:,}행")

    rows = ad.days_in_db(cu)
    print("-" * 58)
    print("쌓인 날짜")
    for r in rows:
        print(f"  {r['day']}  {int(r['cost'] or 0):>8,}원  {r['rows']:,}행")
    if not rows:
        print("  (없음 — --collect 로 먼저 받아오십시오)")
        return 0

    dims = ([(args.dim, args.dim)] if args.dim else DIMS)
    for key, title in dims:
        print("-" * 58)
        print(f"{title} (최근 {max(args.days, 7)}일)")
        for r in ad.by(key, cu, days=max(args.days, 7), limit=args.top):
            k = r["k"]
            if key == "device":
                k = ad.DEVICE.get(k, k)
            elif key == "hour":
                k = f"{k}시"
            print(f"  {str(k or '(없음)')[:30]:32} "
                  f"{int(r['cost'] or 0):>8,}원  클릭 {int(r['clk'] or 0):>5,}"
                  f"  노출 {int(r['imp'] or 0):>8,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
