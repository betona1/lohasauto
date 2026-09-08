"""
**원상품명 + 지정 키워드**로 상품명을 만든다. 사용자가 넣을 말을 직접 줄 때 쓴다.

클릭으로만 만드는 것이 원칙이지만, 후보 표에 쓸 말이 없는 품목(식품이 특히
그렇다)은 사용자가 넣을 말을 정해 준다. 그때는 후보에 없어도 직접 써서
25~29자를 채운다(2026-09-07 사용자 지시).

    python -X utf8 tools/force_titles.py --lcp LCP_LHA_B915067 \
        --words "교회,간식,돌봄,어른,상자,소풍,생일,교육,대용"
    ... --apply                       실제로 저장
    ... --brand 오뚜기                 원상품명에 없어도 앞에 붙일 상표
    ... --only 자숙대두                원상품명에 이 말이 든 상품만

만드는 법
  1) 원상품명의 낱말을 그대로 쓴다 (괄호·기호는 떼고, 붙은 수량은 살린다)
  2) 지정 키워드를 **상품마다 돌아가며** 배치한다 - 형제끼리 달라야 한다
  3) 29자를 넘지 않게 채우고 검색최적화에 물어본다.
     '유의어 반복' 으로 걸리면 그 말이 든 키워드를 빼고 다시 만든다
  4) **미작업만**. 사람이 고친 상품명은 건드리지 않는다
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db                                        # noqa: E402
from app.lohas import (attr_detail, session as ses,       # noqa: E402
                       tabs, title_auto)

MINL, MAXL = 25, 29
# 괄호·구두점만 떼고, 용량·수량은 그대로 살린다
_CLEAN = re.compile(r"[()\[\]{}·,/\\]+")


# '6개입' 은 상품명에 '6개' 로 적는다. '1타'(박스 표시)는 상품명에 넣지 않는다
# (2026-09-07 사용자: "서주 탱글탱글 딸기 40g 6개 이런식으로").
_PACK = re.compile(r"^([0-9]+)(?:개입|입)$")
_BOX = re.compile(r"^[0-9]+(?:타|BOX|박스)$", re.I)
# 용량·수량이 든 낱말 (190ML / 30캔 / 24PET / 1.25L / 324gx6개입)
_QTY = re.compile(r"[0-9]+(?:\.[0-9]+)?\s*(?:KG|G|ML|L|CM|MM|개입|개|입|캔"
                  r"|PET|팩|봉|매|장|정|스틱|T|EA|P)", re.I)


def name_words(pn: str) -> list:
    """원상품명 낱말. 순서를 지킨다 — 앞쪽이 그 상품의 이름이다."""
    out, seen = [], set()
    for w in _CLEAN.sub(" ", pn or "").split():
        w = w.strip(".")
        # 'X 4개' 처럼 곱하기 기호만 홀로 남는 것은 뺀다 - 상품명에 'X' 가
        # 덩그러니 들어간다(2026-09-07).
        if not w or _BOX.match(w) or w.upper() in ("X", "*", "+", "-", "_", "~"):
            continue
        m = _PACK.match(w)
        if m:
            w = m.group(1) + "개"
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lcp", required=True)
    ap.add_argument("--words", required=True,
                    help="넣을 키워드. 쉼표로 구분")
    ap.add_argument("--brand", default="",
                    help="원상품명에 없어도 맨 앞에 붙일 상표")
    ap.add_argument("--only", default="",
                    help="원상품명에 이 말이 든 상품만")
    ap.add_argument("--drop", default="",
                    help="원상품명에서 뺄 낱말. 쉼표로 구분")
    ap.add_argument("--keep-order", action="store_true",
                    help="원상품명 순서를 그대로 둔다 (상표 뒤에 원래 이름)")
    ap.add_argument("--keep", default="",
                    help="원상품명에 있으면 **반드시 남길** 낱말. 쉼표로 구분"
                         " (브랜드처럼 빠지면 안 되는 것)")
    ap.add_argument("--base-max", type=int, default=0,
                    help="원상품명에서 쓸 낱말 수 상한. 브랜드와 용량·수량을"
                         " 먼저 남긴다 (0=전부)")
    ap.add_argument("--folder", default="")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    extra = [w.strip() for w in args.words.split(",") if w.strip()]
    drop = {w.strip() for w in args.drop.split(",") if w.strip()}
    folder = args.folder or db.get_job_folder()
    cli = ses.get_client()

    with db.sqlite_conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT a.l_code, a.product_no, a.title1, l.info_status "
            "FROM lcode_attr a JOIN lcp_lcode l ON l.product_no = a.product_no "
            "WHERE a.lcp_code = ? ORDER BY a.l_code", (args.lcp,))]
    if not rows:
        print("대상이 없습니다"); return

    ok = skip = fail = 0
    for i, r in enumerate(rows):
        L = r["l_code"]
        pn = tabs.fetch_attr(cli.session, r["product_no"]).get(
            "product_name", "")
        if args.only and args.only not in pn:
            print(f"  - {L} '{args.only}' 아님 - 건너뜀 | {pn[:40]}", flush=True)
            skip += 1
            continue
        if r["info_status"] != "미작업":
            print(f"  - {L} {r['info_status']} 건너뜀", flush=True)
            skip += 1
            continue
        live = (attr_detail.fetch_detail(
            cli.session, r["product_no"])["title1"] or "").strip()
        mine = (r["title1"] or "").strip()
        if live and mine and live != mine:
            print(f"  [보호] {L} 사람이 고친 상품명 | {live[:34]}", flush=True)
            skip += 1
            continue

        base = [w for w in name_words(pn) if w not in drop]
        if args.brand:
            # 여러 낱말짜리 상표('바비조아 유기농쌀')도 받는다. 원상품명에
            # 이미 있는 낱말은 두 번 넣지 않는다(2026-09-07 '바비조아 … 바비조아').
            btok = args.brand.split()
            base = [w for w in base if w not in btok]
            base = btok + base
        if args.base_max and not args.keep_order:
            # **브랜드와 용량·수량을 먼저 남긴다.** 음료는 브랜드가 핵심이라
            # 원상품명을 통째로 넣으면 자리가 없어 사용처를 못 붙인다
            # (2026-09-07 사용자: "원본상품명 일부사용, 브랜드가 중요").
            must_keep = [w.strip() for w in args.keep.split(",") if w.strip()]
            keep, rest = [], []
            for w in base:
                pin = (w is base[0] or _QTY.search(w)
                       or any(k == w or k in w for k in must_keep))
                (keep if pin else rest).append(w)
            base = (keep + rest)[:max(args.base_max, len(keep))]
        banned = set()
        done = False
        for attempt in range(4):
            words = []
            for w in base:
                if len(" ".join(words + [w])) <= MAXL:
                    words.append(w)
            seen = set(words)
            order = extra[i % len(extra):] + extra[:i % len(extra)]
            for w in order:
                if w in seen or w in banned:
                    continue
                if len(" ".join(words + [w])) > MAXL:
                    continue
                words.append(w)
                seen.add(w)
            title = " ".join(words)
            page = title_auto.fetch_page(cli.session, r["product_no"])
            chk = title_auto.title_check(cli.session, page, title)
            if chk["ok"]:
                print(f"  {'+' if args.apply else '='} {L} [{len(title)}자] "
                      f"{title}", flush=True)
                if args.apply:
                    title_auto.save_title(cli.session, r["product_no"], title,
                                          [], page["shipping"])
                    d = attr_detail.fetch_detail(cli.session, r["product_no"])
                    d["lcp_code"] = args.lcp
                    d["l_code"] = L
                    db.save_lcode_attr(folder, [d])
                ok += 1
                done = True
                break
            hit = {t for c2 in chk["cause"] for t in (c2.get("term") or [])}
            print(f"    {L} {attempt + 1}차 지적 {sorted(hit)}", flush=True)
            add = {w for w in extra if any(h in w for h in hit)}
            # 지적받은 말이 **원상품명 쪽**일 수도 있다. 사이트가 상표를
            # 막는 경우가 그렇다('송학식품'). 그때는 그 낱말을 빼고 만든다
            # (2026-09-07 LCP_LHA_B915073).
            drop_base = [w for w in base if any(h in w for h in hit)]
            if drop_base:
                base = [w for w in base if w not in drop_base]
                print(f"      원상품명에서 뺌: {drop_base}", flush=True)
            if not add and not drop_base:
                break
            banned |= add
        if not done:
            fail += 1
            print(f"  !! {L} 최적화 통과 실패", flush=True)

    head = "저장" if args.apply else "만들 수 있음"
    print(f"\n{head} {ok}건 / 건너뜀 {skip}건 / 실패 {fail}건", flush=True)


if __name__ == "__main__":
    main()
