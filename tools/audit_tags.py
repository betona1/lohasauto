"""
저장된 태그가 상품과 어긋나지 않는지 점검한다. (읽기 전용)

상품명과 같은 기준이다 — 태그에 박힌 **수량·치수·색상·기능·재질**이 그
L코드 원상품명(+카테고리 이름)에 없으면 어긋난 것이다.
`20L ≠ 2L`, `사각휴지통`을 원형에, `LED풍선`을 LED 아닌 것에 붙이면 안 된다.

    python -X utf8 tools/audit_tags.py                 # 작업폴더 전체
    python -X utf8 tools/audit_tags.py --lcp LCP_LHA_B914790
    python -X utf8 tools/audit_tags.py --fix           # 어긋난 태그만 빼고 저장
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db                                        # noqa: E402
from app.lohas import session as ses, tabs, tag_auto       # noqa: E402
from app.lohas import title_auto                           # noqa: E402


def tag_ok(tag: str, own_specs: set, base: str, common: list,
           rules: dict = None, own_name: str = "") -> bool:
    """
    이 태그를 그 상품에 붙여도 되나.

      1) 규격·수량·색상·기능이 어긋나면 안 된다 (절대규칙 3)
      2) 그 LCP 공통 낱말을 품었거나, 같은 종류여야 한다
         점보롤 케이스에 '냅킨통' 은 다른 물건이다(2026-09-06 사용자 지적).
         '점보롤통' 은 공통 낱말('점보롤')을 품었으니 표기만 다른 같은 물건이다.
    """
    if not tag_auto.spec_ok(tag, own_specs):
        return False
    # 카테고리에 쌓인 사용자 지침 (2026-09-06)
    if rules:
        if any(w in tag and w not in own_name
               for w in rules.get("ban") or ()):
            return False
        if any(w in tag and w not in base
               for w in rules.get("need_name") or ()):
            return False
    if any(w in tag for w in (common or [])):
        return True
    return tag_auto.head_ok(tag, base)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lcp", default="", help="이 LCP 만")
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N건만")
    ap.add_argument("--fix", action="store_true",
                    help="어긋난 태그를 뺀 목록으로 다시 저장한다")
    ap.add_argument("--folder", default="",
                    help="이 작업폴더로. 비우면 지정된 작업폴더"
                         " (마스터폴더가 여럿일 때 쓴다)")
    args = ap.parse_args()

    folder = args.folder or db.get_job_folder()
    sql = ("SELECT lcp_code, l_code, product_no, etc_category "
           "FROM lcode_attr WHERE folder_name = ? AND tag_count > 0")
    a = [folder]
    if args.lcp:
        sql += " AND lcp_code = ?"
        a.append(args.lcp)
    sql += " ORDER BY lcp_code, l_code"
    with db.sqlite_conn() as c:
        rows = [dict(r) for r in c.execute(sql, a)]
    if args.limit:
        rows = rows[:args.limit]
    print(f"작업폴더 {folder} / 태그 있는 L코드 {len(rows):,}건", flush=True)

    cli = ses.get_client()
    bad, n_tags, n_bad = [], 0, 0
    t0 = time.time()
    # 1차 - 읽기만 한다. 공통 낱말은 그 LCP 를 다 읽어야 정해진다
    names_of = {}
    for i, r in enumerate(rows, 1):
        try:
            r["own"] = tabs.fetch_attr(
                cli.session, r["product_no"]).get("product_name", "")
            r["tags"] = [t["text"] for t
                         in tabs.fetch_saved_tags(cli.session, r["product_no"])]
        except Exception:
            r["own"], r["tags"] = "", []
        names_of.setdefault(r["lcp_code"], []).append(r["own"])
        if i % 200 == 0:
            print(f"  {i}/{len(rows)} 읽음 ({time.time() - t0:.0f}초)",
                  flush=True)

    # 2차 - 판정
    commons = {k: title_auto.common_words([x for x in v if x])
               for k, v in names_of.items()}
    for r in rows:
        base = title_auto.spec_base(r["own"], str(r.get("etc_category") or ""))
        own = tag_auto.specs_of(base)
        n_tags += len(r["tags"])
        rules = db.cat_rules(str(r.get("etc_category") or ""), "tag")
        wrong = [t for t in r["tags"]
                 if not tag_ok(t, own, base, commons.get(r["lcp_code"], []),
                               rules, r["own"])]
        if wrong:
            n_bad += len(wrong)
            r["wrong"] = wrong
            bad.append(r)

    print(f"\n태그 {n_tags:,}개 중 어긋남 {n_bad}개 / L코드 {len(bad)}건 "
          f"/ LCP {len({b['lcp_code'] for b in bad})}종", flush=True)
    for b in bad[:30]:
        print(f"  {b['l_code']} {b['wrong']}", flush=True)
        print(f"      원: {b['own'][:52]}", flush=True)
    if not bad or not args.fix:
        if bad:
            print("\n--fix 를 주면 어긋난 태그만 빼고 다시 저장합니다.", flush=True)
        return

    ok = fail = 0
    for i, b in enumerate(bad, 1):
        keep = [t for t in b["tags"] if t not in b["wrong"]]
        if not keep:
            print(f"  - {b['l_code']} 남는 태그가 없어 건너뜁니다", flush=True)
            continue
        try:
            res = tabs.tag_search(cli.session, b["product_no"], keep)
            codes = {t["text"].upper(): t["code"] for t in res["ok"]}
            for t in res["x"]:
                codes.setdefault(t["text"].upper(), -1)
            payload = [{"text": n, "code": codes.get(n.upper(), -1)}
                       for n in keep]
            tabs.save_tags(cli.session, b["product_no"], payload)
            ok += 1
            print(f"  [{i}/{len(bad)}] {b['l_code']} -{len(b['wrong'])}개 "
                  f"-> {len(keep)}개", flush=True)
        except Exception as e:
            fail += 1
            print(f"  !! {b['l_code']} {str(e)[:70]}", flush=True)
    print(f"\n고침 {ok}건 / 실패 {fail}건 ({(time.time() - t0) / 60:.1f}분)",
          flush=True)


if __name__ == "__main__":
    main()
