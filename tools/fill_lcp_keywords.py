"""
LCP 의 **키워드 관리(희망검색어)** 에 상품명 낱말을 넣는다.

로하스는 이 키워드를 바탕으로 태그·상품명 후보를 만들어 준다. 후보가
1~2개뿐이라 태그를 못 채우는 LCP 가 있는데, 그 LCP 상품들의 이름을
띄어쓰기 단위로 쪼개 넣어주면 후보가 넓어진다(2026-09-06 사용자 지시).

    python -X utf8 tools/fill_lcp_keywords.py --lcp LCP_LHA_B915353
    python -X utf8 tools/fill_lcp_keywords.py --lcp LCP_LHA_B915353 --apply
    python -X utf8 tools/fill_lcp_keywords.py --apply --few 3   # 태그후보 3개 미만 전부

낱말은 **줄바꿈으로 구분**해 넣는다. 기존 키워드는 지우지 않고 더한다.
"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db                                        # noqa: E402
from app.lohas import fix10, session as ses, tabs         # noqa: E402

_W = re.compile(r"[0-9A-Za-z가-힣]+")
# 넣어봐야 검색에 안 잡히는 말
STOP = {"세트", "종", "개", "입", "매", "팩", "봉", "포", "본품", "사은품",
        "색상랜덤", "랜덤", "옵션", "선택", "택1", "신상", "정품", "무료배송"}


def words_of(names: list) -> list:
    """상품명들을 띄어쓰기 단위로 쪼개 중복 없이 모은다."""
    out, seen = [], set()
    for nm in names:
        for w in _W.findall(nm or ""):
            k = w.upper()
            if len(w) < 2 or w.isdigit() or w in STOP or k in seen:
                continue
            seen.add(k)
            out.append(w)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lcp", default="", help="이 LCP 만")
    ap.add_argument("--apply", action="store_true", help="실제로 저장한다")
    ap.add_argument("--few", type=int, default=0,
                    help="태그 후보가 이 수 미만인 LCP 전부 (예: 3)")
    ap.add_argument("--folder", default="", help="작업폴더")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    folder = args.folder or db.get_job_folder()
    # 키워드 관리는 **광고상품(LCP) 단위**다. L코드의 product_no 가 아니라
    # lcp_product.product_id 를 써야 한다(2026-09-06 실측).
    sql = ("SELECT a.lcp_code, p.product_id, COUNT(*) n "
           "FROM lcode_attr a JOIN lcp_product p ON p.lcp_code = a.lcp_code "
           "WHERE a.folder_name = ? AND a.cat_saved = 1 "
           "  AND p.product_id IS NOT NULL AND p.product_id <> ''")
    a = [folder]
    if args.lcp:
        sql += " AND a.lcp_code = ?"
        a.append(args.lcp)
    sql += " GROUP BY a.lcp_code ORDER BY a.lcp_code"
    with db.sqlite_conn() as c:
        lcps = [dict(r) for r in c.execute(sql, a)]

    if args.few:
        with db.sqlite_conn() as c:
            thin = {r["lcp_code"] for r in c.execute(
                "SELECT lcp_code FROM cat_keyword WHERE kind='tag' "
                "GROUP BY lcp_code HAVING COUNT(*) < ?", (args.few,))}
            have = {r["lcp_code"] for r in c.execute(
                "SELECT DISTINCT lcp_code FROM cat_keyword")}
        # 백업에 아예 없는 LCP(표가 비어 있던 곳)도 대상이다
        lcps = [x for x in lcps
                if x["lcp_code"] in thin or x["lcp_code"] not in have]
    if args.limit:
        lcps = lcps[:args.limit]
    print(f"작업폴더 {folder} / 대상 LCP {len(lcps):,}종", flush=True)
    if not lcps:
        return

    cli = ses.get_client()
    t0 = time.time()
    ok = skip = fail = 0
    for i, g in enumerate(lcps, 1):
        lcp = g["lcp_code"]
        try:
            rows = db.lcode_rows_of(lcp)
            names = []
            for r in rows:
                names.append(tabs.fetch_attr(
                    cli.session, r["product_no"]).get("product_name", ""))
            ws = words_of(names)
            if not ws:
                skip += 1
                continue
            if not args.apply:
                cur = fix10.read_keywords(cli.session, g["product_id"])
                add = [w for w in ws if w not in cur]
                print(f"  = {lcp} 지금 {len(cur)}개 -> +{len(add)}개", flush=True)
                print(f"      {', '.join(add[:20])}", flush=True)
                ok += 1
                continue
            res = fix10.set_keywords(cli.session, g["product_id"], ws)
            ok += 1
            print(f"  + {lcp} {res['before']}개 -> {res['after']}개 "
                  f"(+{res['added']})", flush=True)
        except Exception as e:
            fail += 1
            print(f"  !! {lcp} {str(e)[:70]}", flush=True)
        if i % 20 == 0:
            print(f"  --- {i}/{len(lcps)} ({(time.time() - t0) / 60:.1f}분)",
                  flush=True)

    head = "넣음" if args.apply else "넣을 수 있음"
    print(f"\n{head} {ok}종 / 건너뜀 {skip}종 / 실패 {fail}종 "
          f"({(time.time() - t0) / 60:.1f}분)", flush=True)


if __name__ == "__main__":
    main()
