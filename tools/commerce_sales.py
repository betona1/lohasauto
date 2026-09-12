"""
네이버 커머스 API 로 **스마트스토어 오늘 매출**을 받는다.

주문 DB 는 수집 주기가 있어 당일이 비어 있다. 오늘 얼마 팔렸는지는
스토어에서 직접 받아야 한다(2026-09-12 사용자).

    python -X utf8 tools/commerce_sales.py              # 오늘
    python -X utf8 tools/commerce_sales.py --date 2026-09-11
    python -X utf8 tools/commerce_sales.py --days 3     # 오늘부터 거슬러
    python -X utf8 tools/commerce_sales.py --save       # ad_sales 에 저장

키는 `.env` 의 COMMERCE_CLIENT_ID / COMMERCE_CLIENT_SECRET.
"""
import argparse
import datetime
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from app import config, db                              # noqa: E402
from app.lohas import ad_sales, commerce                # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    db.init_db()
    if not commerce.available():
        print("!! .env 에 COMMERCE_CLIENT_ID / COMMERCE_CLIENT_SECRET 없음")
        return 1
    print(f"스토어 {config._str('COMMERCE_STORE_NAME')} "
          f"({config._str('COMMERCE_STORE_URL')})")

    days = ([args.date] if args.date else
            [(datetime.date.today() - datetime.timedelta(days=b)).isoformat()
             for b in range(args.days)])
    cmap = ad_sales._code_map()
    for d in days:
        try:
            r = commerce.sales(d)
        except Exception as e:
            print(f"  {d} 실패: {str(e)[:150]}")
            continue
        print(f"  {d}  주문 {r['orders']:,}건 · 수량 {r['qty']:,} · "
              f"{r['amount']:,}원")
        rows = sorted(r["by_product"].items(),
                      key=lambda kv: -kv[1]["amount"])[:args.top]
        for code, v in rows:
            lcp = (cmap.get(code) or ("", ""))[0]
            print(f"     {v['amount']:>8,}원 x{v['qty']:<3} "
                  f"{(lcp or '-'):20} {v['name'][:30]}")
        if args.save and r["orders"]:
            commerce.save(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
