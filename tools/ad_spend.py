"""
네이버 검색광고 지출 수집 · 집계 — LCP별 / 날짜별.

읽기 전용 API 라 광고를 건드리지 않는다.

    python -X utf8 tools/ad_spend.py --collect            # 최근 14일 수집
    python -X utf8 tools/ad_spend.py --collect --days 30
    python -X utf8 tools/ad_spend.py                      # 저장된 것 보기
    python -X utf8 tools/ad_spend.py --lcp                # LCP별 순위
"""
import argparse
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from app import db                                    # noqa: E402
from app.lohas import ad_spend, searchad as sa        # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--lcp", action="store_true", help="LCP별 순위만")
    args = ap.parse_args()

    db.init_db()
    if not sa.available():
        print("!! .env 에 네이버 검색광고 API 키가 없습니다")
        return 1

    if args.collect:
        print(f"수집 — 최근 {args.days}일 (광고계정 "
              f"{', '.join(sa.customers())})")
        r = ad_spend.collect_all(days=args.days)
        print(f"=> {r['rows']:,}행 저장 · 기간 지출 {r['cost']:,}원")

    t = ad_spend.today_cost()
    print("=" * 58)
    print(f"오늘({t['day']}) 광고비 {t['cost']:,}원   "
          f"클릭 {t['clk']:,} · 노출 {t['imp']:,}")

    if not args.lcp:
        print("-" * 58)
        print("날짜별")
        rows = ad_spend.by_day(args.days)
        if not rows:
            print("  (기록 없음 — --collect 로 먼저 받아오십시오)")
        for r in rows:
            print(f"  {r['day']}  {int(r['cost'] or 0):>9,}원  "
                  f"클릭 {int(r['clk'] or 0):>6,}  노출 {int(r['imp'] or 0):>9,}")

    print("-" * 58)
    print(f"LCP별 (최근 {args.days}일)")
    rows = ad_spend.by_lcp(args.days)
    if not rows:
        print("  (지출이 있는 광고그룹이 아직 없습니다)")
    for r in rows:
        print(f"  {r['lcp_code']:22} {int(r['cost'] or 0):>9,}원  "
              f"클릭 {int(r['clk'] or 0):>6,}  {r['days']}일")
    return 0


if __name__ == "__main__":
    sys.exit(main())
