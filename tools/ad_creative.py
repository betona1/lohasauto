"""
광고 소재(쇼핑 상품형) 수집 — **검토 상태 + 로하스 L코드**.

    python -X utf8 tools/ad_creative.py --collect      # 전체 받아오기
    python -X utf8 tools/ad_creative.py                # 보류/검토중 목록
    python -X utf8 tools/ad_creative.py --all          # 전체 목록
    python -X utf8 tools/ad_creative.py --lcode        # L코드만 한 줄씩

소재 = 스마트스토어 상품 하나. 소재 응답에 상품번호가 들어 있어
로하스 L코드와 상품명으로 맞춘다(일치도 1.0 이면 같은 상품이다).
읽기 전용이라 광고를 바꾸지 않는다.
"""
import argparse
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from app import db                                          # noqa: E402
from app.lohas import ad_account, ad_creative as ac          # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--customer", default="")
    ap.add_argument("--all", action="store_true", help="승인된 것도 전부")
    ap.add_argument("--lcode", action="store_true", help="L코드만 출력")
    ap.add_argument("--status", default="", help="이 상태만 (보류/검토중/대기)")
    args = ap.parse_args()

    db.init_db()
    cu = args.customer or (ad_account.main_account() or {}).get("customer_id")
    if not cu:
        print("!! 메인 광고계정이 없습니다"); return 1

    if args.collect:
        r = ac.collect(cu)
        print(f"=> 그룹 {r['groups']:,} · 소재 {r['ads']:,}개")

    print("-" * 62)
    for r in ac.counts(cu):
        print(f"  {r['status_ko']:8} {r['n']:>6,}개")

    rows = (ac._q("SELECT * FROM ad_creative WHERE customer_id=?"
                  " ORDER BY status, lcp_code", (str(cu),))
            if args.all else ac.problems(cu))
    if args.status:
        rows = [r for r in rows if r["status_ko"] == args.status]

    if args.lcode:
        for r in rows:
            if r["l_code"]:
                print(r["l_code"])
        return 0

    print("-" * 62)
    print(f"{len(rows):,}개")
    for r in rows:
        print(f"  {r['status_ko']:6} {r['lcp_code']:20} "
              f"{r['l_code'] or '(못찾음)':10} {r['product_name'][:34]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
