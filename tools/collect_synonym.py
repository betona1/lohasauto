"""
로하스 동의어 사전 수집.

로하스는 상품명 키워드를 형태소로 쪼개면서 표기를 대표어로 바꾼다.

    relKeyword '스텐브러쉬'  ->  terms ['스테인리스', '브러쉬']
    relKeyword '자동브러쉬'  ->  terms ['오토매틱', '브러쉬']

그래서 '스텐...' 을 눌러도 이미 '스테인리스' 가 들어가 있으면 화면에 아무
변화가 없다. 어떤 키워드가 헛클릭인지 미리 알려면 이 대응이 필요하다.

상품명 탭 HTML 안의 `titleKwData` 에 relKeyword 와 terms 가 같이 들어 있어,
탭을 열어보기만 하면 대응이 그대로 나온다. 저장은 하지 않는다(읽기 전용).

    python -X utf8 tools/collect_synonym.py --limit 50
    python -X utf8 tools/collect_synonym.py            # 작업폴더 전체
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db                                        # noqa: E402
from app.lohas import session as ses, title_auto          # noqa: E402


def targets(limit: int = 0, folder: str = None) -> list:
    """
    카테고리가 저장된 L코드. LCP 마다 한 건씩만 본다 — 같은 LCP 는 후보
    목록이 같아서 더 봐야 새로 나오는 게 없다.
    """
    folder = folder or db.get_job_folder()
    sql = ("SELECT lcp_code, MIN(l_code) l_code, MIN(product_no) product_no "
           "FROM lcode_attr WHERE cat_saved = 1")
    args = []
    if folder:
        sql += " AND folder_name = ?"
        args.append(folder)
    sql += " GROUP BY lcp_code ORDER BY lcp_code"
    with db.sqlite_conn() as c:
        rows = [dict(r) for r in c.execute(sql, args)]
    return rows[:limit] if limit else rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N종만")
    ap.add_argument("--min-seen", type=int, default=2,
                    help="이 횟수 이상 본 것만 요약에 보여준다")
    args = ap.parse_args()

    rows = targets(args.limit)
    print(f"대상 {len(rows):,}종 (LCP 당 1건)", flush=True)

    cli = ses.get_client()
    t0 = time.time()
    total, fail = 0, 0
    for i, r in enumerate(rows, 1):
        try:
            page = title_auto.fetch_page(cli.session, r["product_no"])
            pairs = title_auto.mine_synonyms(page["candidates"])
            if pairs:
                db.save_synonyms(pairs)
                total += len(pairs)
        except Exception as e:
            fail += 1
            if fail <= 5:
                print(f"  ! {r['lcp_code']} {str(e)[:60]}", flush=True)
        if i % 20 == 0:
            el = time.time() - t0
            print(f"  {i}/{len(rows)}  대응 {total:,}쌍 누적 "
                  f"({el / 60:.1f}분, 남은 {(el / i) * (len(rows) - i) / 60:.0f}분)",
                  flush=True)

    print(f"\n수집 {total:,}쌍 / 실패 {fail}건 ({(time.time() - t0) / 60:.1f}분)",
          flush=True)

    m = db.synonym_map(min_seen=args.min_seen)
    print(f"\n=== {args.min_seen}회 이상 본 대응 {len(m)}개 ===", flush=True)
    with db.sqlite_conn() as c:
        for r in c.execute(
                "SELECT surface, normal, seen, example FROM synonym "
                "WHERE seen >= ? ORDER BY seen DESC, surface LIMIT 60",
                (args.min_seen,)):
            print(f"  {r['surface']:14} = {r['normal']:16} {r['seen']:>4}회"
                  f"  (예: {r['example']})", flush=True)


if __name__ == "__main__":
    main()
