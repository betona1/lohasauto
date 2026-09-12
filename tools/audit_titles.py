"""
**상품명 점검 · 재작업 일괄** — 지금까지 쌓은 규칙으로 전부 다시 본다.

지적받아 쌓아둔 규칙(카테고리별 `cat_rule`, 상표 사전 `title_ban`,
규격 목록 `_SPEC_RE`, 숫자·행사표기)을 **이미 저장된 상품명에 그대로 대본다.**
어긋나면 뚱딴지같은 말이 들어간 것이다 — 콩국수에 우동, 전복죽에 팥죽,
자숙대두에 랜틸콩이 그랬다(2026-09-07 사용자 지적).

    python -X utf8 tools/audit_titles.py --food                 # 식품만 점검
    python -X utf8 tools/audit_titles.py --food --redo --apply  # 찾아서 다시 만들기
    python -X utf8 tools/audit_titles.py --cat 식품/음료 --redo --apply

**바꾸기 전에 지금 상품명을 `title_backup` 에 남긴다.** 되돌리려면
`--restore <run_id>`.

대상은 **상품정보 미작업**뿐이다. 사람이 고친 상품명(사이트 값이 우리
기록과 다른 것)은 손대지 않는다.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db                                        # noqa: E402
from app.lohas import (attr_detail, session as ses,       # noqa: E402
                       tabs, tag_auto, title_auto)

_WORD = None


def words_of(t: str) -> list:
    import re

    global _WORD
    if _WORD is None:
        _WORD = re.compile(r"[0-9A-Za-z가-힣]+")
    return _WORD.findall(t or "")


def problems(title: str, own: str, cid: str, rules: dict,
             bans: set) -> list:
    """
    이 상품명이 규칙에 어긋나는 곳. 없으면 빈 목록.

    판단 기준은 **그 상품의 원상품명 + 카테고리 이름**이다. 거기 없는 말을
    상품명이 주장하고 있으면 근거가 없는 것이다.
    """
    base = (own or "") + " " + (db.category_name(cid) or "")
    out = []
    # 1) 규격·종류·색상·기능 — 원상품명에 없으면 못 쓴다
    #
    # **양쪽 다 `specs_of` 로 뽑아 견준다.** 글자로만 대면 표기가 다른 같은
    # 말이 걸린다 - 원상품명 '스테인레스' 를 두고 상품명 '스텐' 을 근거없음
    # 으로, '미니금고' 를 두고 '소형' 을 근거없음으로 잡았다(2026-09-09).
    # 저장·태그 쪽 판정(`spec_ok`)과 같은 기준이어야 한다.
    own_specs = tag_auto.specs_of(base)
    for sp in sorted(tag_auto.specs_of(title)):
        if sp not in own_specs:
            out.append(f"규격 '{sp}'")
    # 2) 숫자·행사표기
    import re as _re

    for w in _re.findall(r"[0-9A-Za-z가-힣.]+", title or ""):
        w = w.strip(".")
        if not w:
            continue
        if not tag_auto.numbers_ok(w, base):
            out.append(f"숫자 '{w}'")
    # 3) 카테고리에 쌓인 지침
    for w in sorted(rules.get("need_name") or ()):
        if w in title and w not in base:
            out.append(f"근거없음 '{w}'")
    for w in sorted(rules.get("ban") or ()):
        if w in title and w not in own:
            out.append(f"금지어 '{w}'")
    # 4) 상표 사전
    for b in sorted(bans):
        if b in title and b not in own:
            out.append(f"상표 '{b}'")
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def targets(food: bool, cat: str, folder: str, lcp: str) -> list:
    sql = ("SELECT a.folder_name, a.lcp_code, a.l_code, a.product_no, "
           "       a.etc_category, a.title1 "
           "FROM lcode_attr a JOIN lcp_lcode l ON l.product_no = a.product_no "
           "WHERE l.info_status = '미작업' AND a.cat_saved = 1 "
           "  AND a.title1 <> ''")
    args = []
    if folder:
        sql += " AND a.folder_name = ?"
        args.append(folder)
    if lcp:
        sql += " AND a.lcp_code = ?"
        args.append(lcp)
    sql += " ORDER BY a.lcp_code, a.l_code"
    with db.sqlite_conn() as c:
        rows = [dict(r) for r in c.execute(sql, args)]
    if food:
        cat = cat or "식품"
    if cat:
        rows = [r for r in rows
                if (db.category_name(r["etc_category"]) or "").startswith(cat)]
    return rows


def restore(run_id: str, apply_it: bool):
    """백업한 상품명으로 되돌린다."""
    rows = db.title_backups(run_id)
    if not rows:
        print(f"'{run_id}' 백업이 없습니다"); return
    cli = ses.get_client()
    ok = 0
    for r in rows:
        page = title_auto.fetch_page(cli.session, r["product_no"])
        print(f"  {'+' if apply_it else '='} {r['l_code']} <- {r['title1']}",
              flush=True)
        if apply_it:
            title_auto.save_title(cli.session, r["product_no"], r["title1"],
                                  [], page["shipping"])
            ok += 1
    print(f"\n되돌림 {ok}건 / 백업 {len(rows)}건", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--food", action="store_true", help="식품 카테고리만")
    ap.add_argument("--cat", default="", help="카테고리 이름이 이걸로 시작하는 것")
    ap.add_argument("--folder", default="")
    ap.add_argument("--lcp", default="")
    ap.add_argument("--redo", action="store_true", help="찾은 것을 다시 만든다")
    ap.add_argument("--apply", action="store_true", help="실제로 저장한다")
    ap.add_argument("--restore", default="", help="이 run_id 로 되돌린다")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.restore:
        restore(args.restore, args.apply)
        return

    rows = targets(args.food, args.cat, args.folder, args.lcp)
    if args.limit:
        rows = rows[:args.limit]
    print(f"점검 대상 {len(rows):,}건 / "
          f"{len({r['lcp_code'] for r in rows}):,}종 LCP", flush=True)
    if not rows:
        return

    cli = ses.get_client()
    bans = db.title_bans()
    rule_cache = {}
    bad, t0 = [], time.time()
    for i, r in enumerate(rows, 1):
        cid = str(r["etc_category"])
        if cid not in rule_cache:
            rule_cache[cid] = db.cat_rules(cid, "title")
        try:
            own = tabs.fetch_attr(cli.session, r["product_no"]).get(
                "product_name", "")
        except Exception as e:
            print(f"  !! {r['l_code']} {str(e)[:50]}", flush=True)
            continue
        why = problems(r["title1"], own, cid, rule_cache[cid], bans)
        if why:
            r["product_name"] = own
            r["reason"] = ", ".join(why[:6])
            bad.append(r)
            print(f"  x {r['lcp_code']} {r['l_code']}", flush=True)
            print(f"      원 : {own[:50]}", flush=True)
            print(f"      명 : {r['title1']}", flush=True)
            print(f"      탈 : {r['reason']}", flush=True)
        if i % 100 == 0:
            print(f"  --- {i}/{len(rows)} ({time.time() - t0:.0f}초)",
                  flush=True)

    print(f"\n어긋난 상품명 {len(bad):,}건 / 점검 {len(rows):,}건 "
          f"({(time.time() - t0) / 60:.1f}분)", flush=True)
    if not bad or not args.redo:
        if bad:
            print("다시 만들려면 --redo --apply 를 주십시오", flush=True)
        return

    run_id = time.strftime("audit-%Y%m%d-%H%M%S")
    n = db.save_title_backup(run_id, bad)
    print(f"백업 {n}건 -> run_id {run_id}"
          f"   (되돌리기: --restore {run_id} --apply)", flush=True)
    if not args.apply:
        print("드라이런입니다. --apply 를 주면 다시 만들어 저장합니다.",
              flush=True)
        return

    # LCP 단위로 다시 만든다 - 형제끼리 겹치지 않게 하려면 함께 봐야 한다
    from collections import OrderedDict

    groups = OrderedDict()
    for r in bad:
        groups.setdefault(r["lcp_code"], []).append(r)
    print(f"\n다시 만들기 — {len(groups)}종 LCP", flush=True)
    for lcp in groups:
        # --no-ai : AI 선별을 쓰면 근거 없는 말이 다시 들어온다(부품함에
        #           '셋톱박스·팬트리'). 규칙만으로 만든다(2026-09-09).
        # --force : 여기 걸린 것은 이미 어긋난 상품명이다. 백업은 위에서 했다.
        # --no-tag: 태그는 따로 돌린다.
        os.system(
            f'python -X utf8 -u tools/fill_titles.py --lcp {lcp} '
            f'--folder "{groups[lcp][0]["folder_name"]}" '
            # --any-image : 점검은 이미지 상태를 안 가리는데 재작성만 가리면
            #               '어긋났다고 잡아놓고 못 고치는' 건이 남는다
            #               (2026-09-09 B913965·B915321).
            f'--redo --force --no-tag --no-ai --any-image --apply')


if __name__ == "__main__":
    main()
