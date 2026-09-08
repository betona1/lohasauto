"""
로하스 키워드 백업 — 태그·상품명 후보 표를 통째로 긁어 `cat_keyword` 에 넣는다.

로하스에서 키워드가 지워지는 일이 있다(2026-09-06 LCP_LHA_B914879). 표는
그 상품의 저장된 카테고리를 기준으로 사이트가 만들어 주는 것이라, 지워지면
무엇이 있었는지 알 방법이 없다. **주기적으로 백업해 둔다.**

    python -X utf8 tools/backup_keywords.py            # 아직 안 받은 LCP 만
    python -X utf8 tools/backup_keywords.py --redo     # 전부 다시
    python -X utf8 tools/backup_keywords.py --lcp LCP_LHA_B914879
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db                                        # noqa: E402
from app.lohas import cat_keyword, session as ses         # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--redo", action="store_true", help="이미 받은 것도 다시")
    ap.add_argument("--lcp", default="", help="이 LCP 만")
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N종만")
    ap.add_argument("--titles", type=int, default=1,
                    help="상품명 탭을 몇 개까지 (기본 1 = 상품명1)")
    args = ap.parse_args()

    folder = db.get_job_folder()
    rows = cat_keyword.targets(db, folder, redo=args.redo, only=args.lcp)
    if args.limit:
        rows = rows[:args.limit]
    have = len(db.cat_keyword_lcps())
    print(f"작업폴더 {folder}", flush=True)
    print(f"백업된 LCP {have:,}종 / 이번 대상 {len(rows):,}종", flush=True)
    if not rows:
        print("받을 것이 없습니다.", flush=True)
        return

    cli = ses.get_client()
    t0 = time.time()
    res = cat_keyword.collect_folder(db, cli.session, rows,
                                     titles=args.titles, log=print)
    print(f"\n완료 — LCP {res.get('ok', 0):,}종 / 실패 {res.get('fail', 0)} "
          f"/ 표가 빈 곳 {res.get('empty', 0)} "
          f"({(time.time() - t0) / 60:.1f}분)", flush=True)
    with db.sqlite_conn() as c:
        n = c.execute("SELECT COUNT(*) FROM cat_keyword").fetchone()[0]
        m = c.execute("SELECT COUNT(DISTINCT lcp_code) FROM cat_keyword").fetchone()[0]
    print(f"백업 누적 — 키워드 {n:,}개 / LCP {m:,}종", flush=True)


if __name__ == "__main__":
    main()
