"""
카테고리에 딸린 **총 용량**을 원상품명에서 읽어 채운다.

식품·생활용품 카테고리는 용량(g·ml·매·개)을 같이 넣어야 한다. 지금까지는
"사람이 입력해야 함"으로 분류해 자동에서 빼뒀는데, 원상품명에 적혀 있으면
그대로 계산할 수 있다.

    800G 12개   ->  800 × 12 = 9,600 g
    1kg X2      -> 1000 × 2  = 2,000 g

**모르겠으면 넣지 않는다.** 용량이 틀리면 반품 사유다 —
`50T`(티백 수), `포션(15입)`(1회분 미상), 용량 표기가 둘 이상인 것은 남긴다.

같은 카테고리로 다시 저장하는 것이라 **상품명·태그는 지워지지 않는다**
(2026-09-06 L8109660 으로 확인). 카테고리를 *바꿀 때만* 초기화된다.

    python -X utf8 tools/fill_capacity.py                  # 계획만
    python -X utf8 tools/fill_capacity.py --apply
    python -X utf8 tools/fill_capacity.py --apply --lcp LCP_LHA_B914967
    python -X utf8 tools/fill_capacity.py --apply --all    # 미작업 아닌 것도
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db                                        # noqa: E402
from app.lohas import (attr_detail, category, session as ses,  # noqa: E402
                       tabs)


def unit_of(cid: str) -> str:
    """그 카테고리의 용량 단위. 수집해둔 후보 목록에서 읽는다."""
    with db.sqlite_conn() as c:
        r = c.execute("SELECT unit FROM lcp_category WHERE code = ? "
                      "AND unit IS NOT NULL AND unit <> '' LIMIT 1",
                      (str(cid),)).fetchone()
    return (r["unit"] if r else "") or ""


def site_targets(client, folder: str) -> list:
    """
    로하스의 **「작업완료-총용량확인」** 검색 결과를 그대로 대상으로 삼는다.

    우리 DB 로 추정하면 빠지는 게 있다 — 594 폴더에서 추정 241건 /
    실제 515건이었다(2026-09-07 사용자가 이 필터를 알려줬다).
    """
    rows = category.capacity_todo(client, folder)
    ls = [r["l_code"] for r in rows]
    got = {}
    if ls:
        with db.sqlite_conn() as c:
            got = {r["l_code"]: r["etc_category"] for r in c.execute(
                "SELECT l_code, etc_category FROM lcode_attr "
                "WHERE l_code IN (%s)" % ",".join("?" * len(ls)), ls)}
    return [dict(r, etc_category=got.get(r["l_code"], ""), lcp_code=r["lcp_code"])
            for r in rows if r["product_no"]]


def targets(lcp: str = "", all_rows: bool = False, folder: str = "") -> list:
    folder = folder or db.get_job_folder()
    sql = ("SELECT a.lcp_code, a.l_code, a.product_no, a.etc_category "
           "FROM lcode_attr a "
           "JOIN lcp_lcode l ON l.product_no = a.product_no "
           "WHERE a.folder_name = ? AND a.cat_saved = 1 "
           "  AND a.etc_category <> ''")
    args = [folder]
    if all_rows == "todo":
        # 대표이미지 상태와 무관하게 **상품정보 미작업 전부**.
        # 용량은 카테고리만 저장돼 있으면 넣을 수 있다 - 이미지 승인을
        # 기다릴 이유가 없다(2026-09-07 사용자: "빈거 미작업중인거 죄다").
        sql += " AND l.info_status = '미작업'"
    elif not all_rows:
        sql += " AND l.img_status = '이미지승인완료' AND l.info_status = '미작업'"
    if lcp:
        sql += " AND a.lcp_code = ?"
        args.append(lcp)
    sql += " ORDER BY a.lcp_code, a.l_code"
    with db.sqlite_conn() as c:
        return [dict(r) for r in c.execute(sql, args)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 저장한다")
    ap.add_argument("--lcp", default="", help="이 LCP 만")
    ap.add_argument("--all", action="store_true",
                    help="미작업뿐 아니라 저장완료 건도")
    ap.add_argument("--todo", action="store_true",
                    help="상품정보 미작업 전부 (대표이미지 상태 무관)")
    ap.add_argument("--site", action="store_true",
                    help="로하스의 「작업완료-총용량확인」 검색 결과를 대상으로")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--folder", default="",
                    help="이 작업폴더로. 비우면 지정된 작업폴더"
                         " (마스터폴더가 여럿일 때 쓴다)")
    args = ap.parse_args()

    folder = args.folder or db.get_job_folder()
    if args.site:
        rows = site_targets(ses.get_client(), folder)
        if args.lcp:
            rows = [r for r in rows if r["lcp_code"] == args.lcp]
    else:
        rows = targets(args.lcp, "todo" if args.todo else args.all, folder)
    units = {}
    for r in rows:
        cid = str(r["etc_category"])
        if cid not in units:
            units[cid] = unit_of(cid)
    rows = [r for r in rows if units.get(str(r["etc_category"]))]
    if args.limit:
        rows = rows[:args.limit]
    print(f"작업폴더 {folder}", flush=True)
    print(f"용량 단위가 있는 카테고리의 L코드 {len(rows):,}건", flush=True)
    if not rows:
        return

    cli = ses.get_client()
    ok = skip = unknown = fail = 0
    t0 = time.time()
    for i, r in enumerate(rows, 1):
        L, cid = r["l_code"], str(r["etc_category"])
        unit = units[cid]
        try:
            cur = category.saved_capacity(cli.session, r["product_no"])
            if cur:
                skip += 1
                continue
            pn = tabs.fetch_attr(cli.session,
                                 r["product_no"]).get("product_name", "")
            cap, how = category.parse_capacity(pn, unit)
            if not cap:
                unknown += 1
                print(f"  ? {L} 모름 [{unit}] | {pn[:44]}", flush=True)
                continue
            if not args.apply:
                ok += 1
                print(f"  = {L} {how} = {cap}{unit} | {pn[:40]}", flush=True)
                continue
            category.save_category(cli.session, r["product_no"], L, cid,
                                   unit=unit, total_capacity=str(cap),
                                   current=cid, allow_change=True)
            d = attr_detail.fetch_detail(cli.session, r["product_no"])
            d["lcp_code"] = r["lcp_code"]
            d["l_code"] = L
            db.save_lcode_attr(folder, [d])
            ok += 1
            print(f"  + {L} {how} = {cap}{unit} | {pn[:40]}", flush=True)
        except Exception as e:
            fail += 1
            print(f"  !! {L} {str(e)[:70]}", flush=True)
        if i % 50 == 0:
            print(f"  --- {i}/{len(rows)} ({time.time() - t0:.0f}초)", flush=True)

    head = "넣음" if args.apply else "넣을 수 있음"
    print(f"\n{head} {ok}건 / 이미 있음 {skip}건 / 모름 {unknown}건 / "
          f"실패 {fail}건 ({(time.time() - t0) / 60:.1f}분)", flush=True)
    if not args.apply and ok:
        print("--apply 를 주면 저장합니다.", flush=True)


if __name__ == "__main__":
    main()
