"""
상품명1 을 미작업 건에 채운다.

대상은 화면의 「미작업목록」과 같다 — **대표이미지 승인완료 + 상품정보 미작업**,
그리고 작업폴더 안. 저장 직전에 사이트에서 다시 읽어 상품명이 비어 있을 때만
넣으므로, 사람이 그 사이 작업했어도 덮지 않는다.

만드는 방식은 화면에서 사람이 하는 것과 같다(`app/lohas/title_auto.py`).
키워드를 하나씩 눌러 형태소를 쌓고, 25자 근처에서 멈추고, 검색최적화를
통과해야만 저장한다. 「상품명/태그 저장완료」는 누르지 않는다 — 사람 몫이다.

    python -X utf8 tools/fill_titles.py                # 대상만 세어본다
    python -X utf8 tools/fill_titles.py --apply
    python -X utf8 tools/fill_titles.py --apply --limit 20
    python -X utf8 tools/fill_titles.py --apply --lcp LCP_LHA_B914708
"""
import argparse
import collections
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db                                        # noqa: E402
from app.lohas import (attr_detail, session as ses, tabs,  # noqa: E402
                       tag_auto, title_auto)


def targets(lcp: str = "", folder: str = None, redo: bool = False,
            any_image: bool = False, any_status: bool = False) -> dict:
    """미작업 L코드를 LCP 단위로 묶는다."""
    folder = folder or db.get_job_folder()
    sql = ("SELECT a.lcp_code, a.l_code, a.product_no, a.tag_count, "
           "       a.etc_category, a.title1 "
           "FROM lcode_attr a "
           "JOIN lcp_lcode l ON l.product_no = a.product_no "
           "WHERE a.folder_name = ? AND a.cat_saved = 1")
    if not any_status:
        # 기본은 미작업만. **저장하면 사이트가 저장완료로 넘긴다** -
        # 이미 넘어간 것을 고칠 때만 --done 으로 연다 (2026-09-08).
        sql += " AND l.info_status = '미작업'"
    if not any_image:
        # 기본은 화면의 「미작업목록」과 같은 범위 — 대표이미지 승인완료
        sql += " AND l.img_status = '이미지승인완료'"
    if not redo:
        sql += " AND a.title_saved = 0"
    args = [folder]
    if lcp:
        sql += " AND a.lcp_code = ?"
        args.append(lcp)
    sql += " ORDER BY a.lcp_code, a.l_code"
    groups = collections.OrderedDict()
    with db.sqlite_conn() as c:
        for r in c.execute(sql, args):
            groups.setdefault(r["lcp_code"], []).append(dict(r))
    return groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 저장한다")
    ap.add_argument("--lcp", default="", help="이 LCP 만")
    ap.add_argument("--only", default="",
                    help="이 L코드만 (쉼표로 여럿). 한 LCP 에 다른 상품이"
                         " 섞여 들어온 경우에 쓴다")
    ap.add_argument("--skip", default="",
                    help="이 L코드는 건너뛴다 (쉼표로 여럿)")
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N종만")
    ap.add_argument("--no-ai", action="store_true",
                    help="AI 선별 없이 규칙만 (조회수 순)")
    ap.add_argument("--lcp-file", default="",
                    help="한 줄에 하나씩 적힌 LCP 목록만")
    ap.add_argument("--any-image", action="store_true",
                    help="대표이미지가 아직 안 끝난 것도 (상품정보 미작업이면)")
    ap.add_argument("--redo", action="store_true",
                    help="상품명이 이미 있어도 다시 만든다 (규칙이 바뀌었을 때)")
    ap.add_argument("--min-len", type=int, default=0,
                    help="--redo 와 함께 - 지금 상품명이 이 글자수 이상이면 둔다")
    ap.add_argument("--done", action="store_true",
                    help="이미 '저장완료' 로 넘어간 것도 대상에 넣는다. "
                         "상태는 더 바뀌지 않으므로 값만 고칠 때 쓴다")
    ap.add_argument("--force", action="store_true",
                    help="사람이 고친 상품명도 덮어쓴다. 지금 값은 "
                         "title_backup 에 남긴다 (형제끼리 상품명이 같아 "
                         "다시 만들 때만 쓴다)")
    ap.add_argument("--no-tag", action="store_true",
                    help="태그가 비어 있어도 채우지 않는다")
    ap.add_argument("--folder", default="",
                    help="이 작업폴더로. 비우면 지정된 작업폴더"
                         " (마스터폴더가 여럿일 때 쓴다)")
    args = ap.parse_args()

    folder = args.folder or db.get_job_folder()
    RUN_ID = "redo-" + db.now_str().replace(" ", "_")
    skip_words = [w.strip() for w in
                  db.get_setting("title_skip_words", "").split(",") if w.strip()]
    if skip_words:
        print(f"제외 품목: {', '.join(skip_words)}", flush=True)
    groups = targets(args.lcp, folder, redo=args.redo,
                     any_image=args.any_image, any_status=args.done)
    names = list(groups)
    if args.lcp_file:
        only = {x.strip() for x in open(args.lcp_file, encoding="utf-8") if x.strip()}
        names = [n for n in names if n in only]
    if args.limit:
        names = names[:args.limit]
    only_l = {x.strip() for x in args.only.split(",") if x.strip()}
    skip_l = {x.strip() for x in args.skip.split(",") if x.strip()}
    if only_l or skip_l:
        for n in list(groups):
            g = [r for r in groups[n]
                 if (not only_l or r["l_code"] in only_l)
                 and r["l_code"] not in skip_l]
            if g:
                groups[n] = g
            else:
                groups.pop(n)
        names = [n for n in names if n in groups]

    n_rows = sum(len(groups[n]) for n in names)
    print(f"작업폴더 {folder}", flush=True)
    print(f"대상 {len(names):,}종 / L코드 {n_rows:,}건", flush=True)
    if not args.apply:
        print("\n[드라이런] --apply 를 주면 저장합니다. 앞 10종:", flush=True)
        for n in names[:10]:
            print(f"   {n}  {len(groups[n]):>3}건", flush=True)
        return

    cli = ses.get_client()
    info = {}
    with db.sqlite_conn() as c:
        for r in c.execute("SELECT lcp_code, brand, maker FROM lcp_product"):
            info[r["lcp_code"]] = (r["brand"] or "", r["maker"] or "")

    ok = skip = fail = 0
    t0 = time.time()
    for i, lcp in enumerate(names, 1):
        brand, maker = info.get(lcp, ("", ""))

        # 태그가 비어 있으면 먼저 채운다. 상품명만 넣으면 그 상품은
        # 태그 없이 남는다 - 어차피 같은 화면에서 같이 하는 작업이다.
        if not args.no_tag:
            empty = [r for r in groups[lcp] if not (r.get("tag_count") or 0)]
            if empty:
                rows_t = db.lcode_rows_of(lcp)
                for r in rows_t:
                    r["lcp_code"] = lcp
                try:
                    t = tag_auto.apply_to_rows(
                        cli.session, rows_t, use_ai=not args.no_ai,
                        log=lambda *_: None)
                    if t["ok"]:
                        print(f"    [태그] {lcp} {t['ok']}건 채움", flush=True)
                except Exception as e:
                    print(f"    !! [태그] {lcp} {str(e)[:60]}", flush=True)

        # 그 LCP 상품명들에 공통으로 든 낱말 — 그 물건이 무엇인지다.
        # 상품명에서 빠지면 말이 안 되므로 먼저 넣는다(사용자 2026-09-06).
        names_all = tag_auto.child_names(cli.session, groups[lcp])
        must = title_auto.head_words(names_all, str(
            groups[lcp][0].get("etc_category") or ""))
        if must:
            print(f"    [공통] {', '.join(must[:6])}", flush=True)
        # 절반 넘는 형제가 함께 쓰는 낱말은 구별점이 아니다. 그것을 뺀
        # 나머지가 '이 상품만의 말' 이고, 그걸 먼저 눌러야 형제끼리 다른
        # 상품명이 된다 (2026-09-06 LCP_LHA_B915384 - 16건이 전부 같았다).
        shared = set(title_auto.common_words(names_all, 0.5))

        made = []
        for r, pn0 in zip(groups[lcp], names_all):
            no, L = r["product_no"], r["l_code"]
            try:
                page = title_auto.fetch_page(cli.session, no)
                d0 = attr_detail.fetch_detail(cli.session, no)
                # 사람이 그 사이 넣었으면 건드리지 않는다
                cur = (d0["title1"] or "").strip()
                mine = (r.get("title1") or "").strip()
                # **사람이 고친 것은 건드리지 않는다.**
                # 우리가 저장하면 그 값을 DB(lcode_attr.title1)에도 적어둔다.
                # 지금 사이트 값이 그것과 다르면 사람이 손본 것이다
                # (2026-09-06 사용자 지시 - L1780433 을 덮을 뻔했다).
                if cur and mine and cur != mine and not args.force:
                    skip += 1
                    print(f"    [보호] {L} 사람이 고친 상품명 - 건너뜀: "
                          f"{cur[:30]}", flush=True)
                    continue
                if cur and not mine and not args.force:
                    skip += 1
                    print(f"    [보호] {L} 우리 기록에 없는 상품명 - 건너뜀",
                          flush=True)
                    continue
                if args.force and cur:
                    # 덮기 전에 지금 값을 남긴다. 되돌릴 근거다.
                    db.save_title_backup(
                        RUN_ID, [{**r, "lcp_code": lcp, "title1": cur,
                                  "product_name": pn0,
                                  "reason": "--force 로 다시 만듦"}])
                if not args.redo and cur:
                    skip += 1          # 사람이 넣었거나 이미 만든 것
                    continue
                if args.redo and args.min_len and len(cur) >= args.min_len:
                    skip += 1          # 이미 길이가 맞다 - 손대지 않는다
                    continue
                pn = pn0
                # 사용자가 '이건 하지 말라' 고 한 품목은 건드리지 않는다
                # (2026-09-06 "후추 들어간 제품은 제외할께 하지마셔").
                if any(w and w in pn for w in skip_words):
                    skip += 1
                    print(f"    [제외] {L} 지시로 건드리지 않음 | {pn[:34]}",
                          flush=True)
                    continue
                res = title_auto.build_and_check(
                    cli.session, page, pn, brand=brand, maker=maker,
                    use_ai=not args.no_ai, cid=str(r.get("etc_category") or ""),
                    avoid=title_auto.sibling_words(lcp, L), must=must,
                    own_uniq=set(tag_auto.words_of(pn)) - shared,
                    log=lambda *_: None)
                if not res["ok"] or not res["title"]:
                    # **형제 중복 회피를 풀고 한 번 더.** 형제가 많은 LCP 는
                    # 앞쪽이 좋은 후보를 다 가져가 뒤쪽이 빈칸으로 남는다.
                    # 겹치더라도 상품명이 있는 편이 낫다 - 엑사에서 268건이
                    # 그렇게 비어 있었다(2026-09-09).
                    res = title_auto.build_and_check(
                        cli.session, page, pn, brand=brand, maker=maker,
                        use_ai=not args.no_ai,
                        cid=str(r.get("etc_category") or ""),
                        avoid=None, must=must,
                        own_uniq=set(tag_auto.words_of(pn)) - shared,
                        log=lambda *_: None)
                if not res["ok"] or not res["title"]:
                    fail += 1
                    continue
                title_auto.save_title(
                    cli.session, no, res["title"],
                    [x["relKeyword"] for x in res["picked"]], page["shipping"])
                d = attr_detail.fetch_detail(cli.session, no)
                db.save_task_log({
                    "ts": db.now_str(), "folder_name": folder, "lcp_code": lcp,
                    "l_code": L, "product_no": str(no), "step": "상품명",
                    "action": "자동입력", "status": "ok",
                    "picked": res["title"],
                    "picked_count": len(res["picked"]),
                    "source": "클릭"})
                if d["title1"].strip() == res["title"].strip():
                    ok += 1
                    made.append((L, res["title"]))
                    d["lcp_code"] = lcp
                    d["l_code"] = L
                    db.save_lcode_attr(folder, [d])
                else:
                    fail += 1
            except Exception as e:
                fail += 1
                print(f"  !! {L} {str(e)[:70]}", flush=True)

        el = time.time() - t0
        eta = (el / i) * (len(names) - i) / 60
        head = made[0][1] if made else "-"
        print(f"[{i}/{len(names)}] {lcp} {len(groups[lcp]):>3}건 "
              f"-> 저장 {len(made)}  {head[:40]}"
              f"   ({el / 60:.1f}분 / 남은 {eta:.0f}분)", flush=True)

    print(f"\n완료 — 저장 {ok:,}건 / 건너뜀 {skip:,}건 / 실패 {fail:,}건"
          f" ({(time.time() - t0) / 60:.1f}분)", flush=True)


if __name__ == "__main__":
    main()
