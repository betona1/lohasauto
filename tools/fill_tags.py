"""
태그가 비어 있는 L코드를 전부 채운다.

태그 출처는 로하스 태그 탭의 기본 태그 후보다. 후보가 하나도 없을 때만
상품명 후보에서 1개를 가져온다 (`app/lohas/tag_auto.py`).

저장 직전에 사이트에서 현재 태그를 다시 읽어 비어 있을 때만 넣는다.
그래서 중간에 끊고 다시 돌려도 안전하고, 사람이 손으로 넣어둔 것을
덮지 않는다.

    python -X utf8 tools/fill_tags.py                 # 대상만 세어본다
    python -X utf8 tools/fill_tags.py --apply         # 실제로 넣는다
    python -X utf8 tools/fill_tags.py --apply --limit 5
    python -X utf8 tools/fill_tags.py --apply --lcp LCP_LHA_B914616
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db                                        # noqa: E402
from app.lohas import (attr_detail, session as ses,       # noqa: E402
                       tag_auto)


def targets(lcp: str = "", todo_only: bool = True, only: set = None):
    """
    태그가 비어 있는 L코드를 LCP 단위로 묶는다. LCP 코드 오름차순이다.

    todo_only=True 면 화면의 「미작업목록」과 같은 범위로 좁힌다 —
    대표이미지 승인완료 + 상품정보 미작업. 지금 바로 작업에 들어갈 수
    있는 것들이라 화면에 보이는 순서와 일치한다.
    """
    sql = ("SELECT a.lcp_code, a.l_code, a.product_no, a.etc_category, "
           "       a.tag_count "
           "FROM lcode_attr a ")
    if todo_only:
        sql += ("JOIN lcp_lcode c ON c.product_no = a.product_no "
                "  AND c.img_status = '이미지승인완료' "
                "  AND c.info_status = '미작업' ")
    sql += "WHERE a.cat_saved = 1 AND a.tag_count = 0"
    args = []
    if lcp:
        sql += " AND a.lcp_code = ?"
        args.append(lcp)
    sql += " ORDER BY a.lcp_code, a.l_code"
    groups = {}
    with db.sqlite_conn() as c:
        for r in c.execute(sql, args):
            if only and r["lcp_code"] not in only:
                continue
            groups.setdefault(r["lcp_code"], []).append(dict(r))
    return groups


def redo_targets(only: set):
    """이미 태그가 있어도 다시 잡을 LCP (규칙이 바뀌었을 때 쓴다)."""
    groups = {}
    with db.sqlite_conn() as c:
        for r in c.execute(
                "SELECT lcp_code, l_code, product_no, etc_category, tag_count "
                "FROM lcode_attr WHERE cat_saved = 1 "
                "ORDER BY lcp_code, l_code"):
            if r["lcp_code"] in only:
                groups.setdefault(r["lcp_code"], []).append(dict(r))
    return groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 저장한다")
    ap.add_argument("--lcp", default="", help="이 LCP 만")
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N종만")
    ap.add_argument("--overwrite", action="store_true",
                    help="이미 태그가 있어도 덮어쓴다 (기본은 건너뜀)")
    ap.add_argument("--ai", action="store_true",
                    help="타사 브랜드·안 맞는 기능을 AI 가 걸러낸다")
    ap.add_argument("--lcp-file", default="",
                    help="한 줄에 하나씩 적힌 LCP 목록 파일만 처리")
    ap.add_argument("--fill", action="store_true",
                    help="태그가 5개 미만이면 상품명 후보로 채운다")
    ap.add_argument("--all", action="store_true",
                    help="미작업목록 범위를 넘어 카테고리 저장분 전부")
    args = ap.parse_args()

    only = None
    if args.lcp_file:
        only = {x.strip() for x in open(args.lcp_file, encoding="utf-8")
                if x.strip()}
    if only and args.overwrite:
        groups = redo_targets(only)          # 이미 있는 것도 다시
    else:
        groups = targets(args.lcp, todo_only=not args.all, only=only)
    names = list(groups)
    if args.limit:
        names = names[:args.limit]
    n_rows = sum(len(groups[n]) for n in names)
    print(f"대상 {len(names):,}종 / L코드 {n_rows:,}건", flush=True)

    if not args.apply:
        print("\n[드라이런] --apply 를 주면 저장합니다. 앞 10종:", flush=True)
        for n in names[:10]:
            print(f"   {n}  {len(groups[n]):>3}건  "
                  f"cat={groups[n][0]['etc_category']}", flush=True)
        return

    cli = ses.get_client()
    t0 = time.time()
    ok = fail = skip = 0
    empty = []          # 태그 후보가 아예 없던 LCP

    for i, name in enumerate(names, 1):
        rows = groups[name]
        try:
            res = tag_auto.apply_to_rows(
                cli.session, rows, overwrite=args.overwrite,
                use_ai=args.ai, fill_more=args.fill, log=lambda m: None)
        except Exception as e:
            fail += len(rows)
            print(f"[{i}/{len(names)}] {name} 실패: {str(e)[:70]}", flush=True)
            continue

        ok += res["ok"]
        fail += res["fail"]
        skip += res["skip"]
        if res["ok"] and res["picks"]:
            first = next(iter(res["picks"].values()))
            tags = ", ".join(t["name"] for t in first["tags"][:5])
            src = first["source"]
        else:
            tags, src = "-", "-"
            if not res["ok"] and not res["skip"]:
                empty.append(name)

        # DB 를 사이트 상태에 맞춘다
        out = []
        for r in res["saved"]:
            try:
                d = attr_detail.fetch_detail(cli.session, r["product_no"])
                d["lcp_code"] = name
                d["l_code"] = r["l_code"]
                out.append(d)
            except Exception:
                pass
        if out:
            db.save_lcode_attr(db.get_job_folder(), out)

        el = time.time() - t0
        eta = (el / i) * (len(names) - i) / 60
        print(f"[{i}/{len(names)}] {name} {len(rows):>3}건 "
              f"-> 저장 {res['ok']} 건너뜀 {res['skip']} 실패 {res['fail']}"
              f"  [{src}] {tags}"
              f"   ({el / 60:.1f}분 경과 / 남은 {eta:.0f}분)", flush=True)

    print(f"\n완료 — 저장 {ok:,}건 / 건너뜀 {skip:,}건 / 실패 {fail:,}건"
          f" ({(time.time() - t0) / 60:.1f}분)", flush=True)
    if empty:
        print(f"태그 후보가 없던 LCP {len(empty)}종: {empty[:20]}", flush=True)


if __name__ == "__main__":
    main()
