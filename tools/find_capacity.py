"""
**용량이 있는데 안 들어간 상품**을 찾는다.

용량 단위가 딸린 카테고리(g·ml·매·m …)인데 총 용량이 비어 있고, 원상품명에
용량이 적혀 있어 계산이 되는 것들이다. 그냥 두면 그대로 등록된다.

    python -X utf8 tools/find_capacity.py                    # 작업폴더
    python -X utf8 tools/find_capacity.py --all-folders      # 마스터폴더 전부
    python -X utf8 tools/find_capacity.py --unknown          # 계산 안 되는 것도
    python -X utf8 tools/find_capacity.py --out logs/cap.txt

**단위는 DB(`lcp_category.unit`)에서 읽으므로 사이트를 긁지 않는다.**
저장 여부만 확인이 필요한데, 그건 `--check` 를 줄 때만 상품별로 조회한다
(2026-09-07 사용자: "불필요한 로하스 크롤링은 자제하자").
`--check` 없이는 우리 DB 의 `lcode_attr` 기록으로 판단한다.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db                                        # noqa: E402
from app.lohas import category, session as ses, tabs      # noqa: E402


def unit_map() -> dict:
    """카테고리코드 -> 용량 단위. 수집해둔 후보 목록에서 한 번에 읽는다."""
    with db.sqlite_conn() as c:
        return {str(r["code"]): r["unit"] for r in c.execute(
            "SELECT code, unit FROM lcp_category "
            "WHERE unit IS NOT NULL AND unit <> '' GROUP BY code")}


def rows_of(folder: str, all_rows: bool = False) -> list:
    sql = ("SELECT a.lcp_code, a.l_code, a.product_no, a.etc_category, "
           "       a.folder_name, l.img_status, l.info_status "
           "FROM lcode_attr a JOIN lcp_lcode l ON l.product_no = a.product_no "
           "WHERE a.folder_name = ? AND a.cat_saved = 1 "
           "  AND a.etc_category <> ''")
    args = [folder]
    if not all_rows:
        sql += " AND l.info_status = '미작업'"
    sql += " ORDER BY a.lcp_code, a.l_code"
    with db.sqlite_conn() as c:
        return [dict(r) for r in c.execute(sql, args)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", action="append", default=[])
    ap.add_argument("--all-folders", action="store_true",
                    help="마스터폴더 전부")
    ap.add_argument("--all", action="store_true",
                    help="미작업뿐 아니라 저장완료 건도")
    ap.add_argument("--unknown", action="store_true",
                    help="계산이 안 되는 것도 함께 보여준다")
    ap.add_argument("--site", action="store_true",
                    help="로하스의 「작업완료-총용량확인」 필터로 찾는다"
                         " (정본. DB 추정보다 정확하다)")
    ap.add_argument("--check", action="store_true",
                    help="사이트에서 저장 여부를 하나씩 확인한다(느리다)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="", help="결과를 이 파일에도 적는다")
    args = ap.parse_args()

    folders = list(args.folder)
    if args.all_folders:
        folders = db.list_master_folders()
    if not folders:
        folders = [f for f in [db.get_job_folder()] if f]
    if not folders:
        print("폴더가 없습니다"); return

    units = unit_map()
    cli = ses.get_client()
    t0 = time.time()
    out, todo, unknown, have = [], 0, 0, 0

    for folder in folders:
        if args.site:
            # 로하스 화면의 검색 그대로 — 「이미지승인완료 + 작업완료-총용량확인」
            base = category.capacity_todo(cli or ses.get_client(), folder)
            got = {}
            if base:
                with db.sqlite_conn() as c:
                    ls = [x["l_code"] for x in base]
                    got = {r2["l_code"]: r2["etc_category"] for r2 in c.execute(
                        "SELECT l_code, etc_category FROM lcode_attr "
                        "WHERE l_code IN (%s)" % ",".join("?" * len(ls)), ls)}
            rows = [dict(x, etc_category=got.get(x["l_code"], ""))
                    for x in base if x["product_no"]]
        else:
            rows = [r for r in rows_of(folder, args.all)
                    if units.get(str(r["etc_category"]))]
        if not rows:
            continue
        head = ("로하스 «작업완료-총용량확인»" if args.site
                else "용량 단위가 있는 카테고리")
        print("", flush=True)
        print(f"=== {folder} — {head} {len(rows):,}건 ===",
              flush=True)
        for r in rows:
            if args.limit and todo >= args.limit:
                break
            unit = units.get(str(r.get("etc_category") or ""), "")
            if not unit:
                unit = "g"          # 단위를 모르면 무게로 본다(사이트 목록)
            try:
                pn = tabs.fetch_attr(
                    cli.session,
                    r["product_no"]).get("product_name", "")
            except Exception as e:
                print(f"  !! {r['l_code']} {str(e)[:50]}", flush=True)
                continue
            val, how = category.parse_capacity(pn, unit)
            saved = ""
            if args.check:
                try:
                    saved = category.saved_capacity(cli.session,
                                                    r["product_no"])
                except Exception:
                    saved = ""
            if saved:
                have += 1
                continue
            if not val:
                unknown += 1
                if args.unknown:
                    line = (f"  ? {r['lcp_code']} {r['l_code']} [{unit}] "
                            f"모름 | {pn[:46]}")
                    print(line, flush=True)
                    out.append(line)
                continue
            todo += 1
            line = (f"  = {r['lcp_code']} {r['l_code']} {how} = {val}{unit}"
                    f" | {pn[:46]}")
            print(line, flush=True)
            out.append(line)

    print(f"\n넣을 수 있는데 안 들어간 것 {todo:,}건"
          f" / 계산 안 되는 것 {unknown:,}건"
          + (f" / 이미 있음 {have:,}건" if args.check else "")
          + f"  ({(time.time() - t0) / 60:.1f}분)", flush=True)
    print("넣으려면 : python -X utf8 tools/fill_capacity.py "
          "--folder <폴더> --apply", flush=True)
    if args.out and out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        print(f"목록 저장 -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
