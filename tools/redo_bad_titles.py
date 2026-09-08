"""
상품명이 상품과 어긋난 것을 찾아 다시 만든다.

무엇을 '어긋났다' 고 보는가 — 상품명에 박힌 **규격·수량이 그 L코드의
원상품명에 없는** 경우다. 수량이 틀리면 반품 사유다.

    바지걸이 5P 10개입 ...   <- 한 상품명에 수량이 둘 (원상품명은 10P)
    10개입 ...               <- 원상품명은 3P

    python -X utf8 tools/redo_bad_titles.py                    # 점검만
    python -X utf8 tools/redo_bad_titles.py --apply
    python -X utf8 tools/redo_bad_titles.py --apply --lcp-file logs/redo_qty.txt
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db                                        # noqa: E402
from app.lohas import (attr_detail, session as ses, tabs,  # noqa: E402
                       tag_auto, title_auto)


def is_qty(sp: str) -> bool:
    return sp.endswith("개")


# 틀리면 반품·클레임으로 이어지는 규격들. '스탠드'·'손잡이'·'무선' 처럼
# 어느 상품에나 있을 법한 말은 뺐다 — 그것까지 잡으면 1,478건이 걸리는데
# 대부분 표기 차이다(스탠딩 샌드백 vs 스탠드).
RISKY = {
    # 색상
    "블랙", "화이트", "골드", "실버", "핑크", "그레이", "네이비", "레드",
    "블루", "그린", "베이지", "브라운", "아이보리", "크롬", "투톤",
    # 기능
    "LED", "ONOFF", "온오프", "정수", "필터", "절수", "마사지", "무드등",
    "온도계", "자동잠금", "전동", "충전식", "건전지", "펌프",
    # 주장
    "국내산", "국산", "수입산", "정품", "친환경", "무독성", "무형광",
    "항균", "방수", "살균", "연수", "비타민", "아로마",
    # 설치 자리 (다른 물건)
    "싱크대", "씽크대", "세면대", "욕조", "변기", "비데", "현관문", "방문",
    # 모양 (겉모습이 다르면 바로 보인다)
    "사각", "정사각", "직사각", "원형", "라운드", "타원", "삼각", "육각",
    "페달", "스윙", "뚜껑", "밀폐", "덮개", "접이식", "양면", "단면",
    # 재질
    "스텐", "스테인리스", "유리", "강화유리", "대리석", "인조대리석",
    "알루미늄", "우드", "원목", "실리콘", "아크릴", "PVC", "황동",
}


def risky(sp: str) -> bool:
    """수량·치수·용량은 숫자로 시작하므로 그것만 봐도 가려진다."""
    return is_qty(sp) or sp[:1].isdigit() or sp.upper() in RISKY


def bad_numbers(title: str, own_name: str, cid: str = "") -> list:
    """상품명에 있는데 원상품명에는 없는 숫자. 340g 짜리 키워드가 3.1Kg 에
    붙는 것을 잡는다(2026-09-06)."""
    base = title_auto.spec_base(own_name or "", cid)
    return [n for n in tag_auto._NUM_RE.findall(title or "") if n not in base]


def check(title: str, own_name: str, cid: str = "") -> list:
    """
    상품명이 원상품명과 어긋나는 규격을 돌려준다.

    카테고리 이름도 기준에 넣는다 — '공기청정기필터' 상품의 '필터' 는
    어긋난 게 아니라 그 상품 자체다.
    """
    t = tag_auto.specs_of(title or "")
    o = tag_auto.specs_of(title_auto.spec_base(own_name or "", cid))
    return sorted(x for x in t if x not in o)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 다시 만든다")
    ap.add_argument("--lcp-file", default="", help="LCP 목록 파일만")
    ap.add_argument("--lcp", default="", help="이 LCP 만")
    ap.add_argument("--qty-only", action="store_true", default=True,
                    help="수량 어긋남만 (기본)")
    ap.add_argument("--all-specs", action="store_true",
                    help="수량뿐 아니라 모든 규격 어긋남")
    ap.add_argument("--numbers", action="store_true",
                    help="숫자가 원상품명과 안 맞는 것만")
    ap.add_argument("--todo", action="store_true",
                    help="미작업 건만 (작업완료는 건드리지 않는다)")
    ap.add_argument("--risk", action="store_true",
                    help="틀리면 클레임 나는 규격만 (수량·치수·색상·기능·재질)")
    ap.add_argument("--folder", default="",
                    help="이 작업폴더로. 비우면 지정된 작업폴더"
                         " (마스터폴더가 여럿일 때 쓴다)")
    args = ap.parse_args()

    folder = args.folder or db.get_job_folder()
    only = None
    if args.lcp_file:
        only = {x.strip() for x in open(args.lcp_file, encoding="utf-8")
                if x.strip()}
    sql = ("SELECT a.lcp_code, a.l_code, a.product_no, a.title1, "
           "       a.etc_category FROM lcode_attr a "
           "JOIN lcp_lcode l ON l.product_no = a.product_no "
           "WHERE a.folder_name = ? AND a.title1 <> ''")
    a = [folder]
    if args.todo:
        sql += (" AND l.img_status = '이미지승인완료'"
                " AND l.info_status = '미작업'")
    if args.lcp:
        sql += " AND a.lcp_code = ?"
        a.append(args.lcp)
    with db.sqlite_conn() as c:
        rows = [dict(r) for r in c.execute(sql, a)]
    if only:
        rows = [r for r in rows if r["lcp_code"] in only]
    print(f"작업폴더 {folder} / 검사 대상 {len(rows):,}건", flush=True)

    cli = ses.get_client()
    bad = []
    t0 = time.time()
    for i, r in enumerate(rows, 1):
        try:
            pn = tabs.fetch_attr(cli.session,
                                 r["product_no"]).get("product_name", "")
        except Exception:
            continue
        r["own"] = pn
        miss = check(r["title1"], pn, str(r.get("etc_category") or ""))
        if args.numbers:
            miss = []
        elif args.risk:
            miss = [x for x in miss if risky(x)]
        elif not args.all_specs:
            miss = [x for x in miss if is_qty(x)]
        nb = bad_numbers(r["title1"], pn, str(r.get("etc_category") or ""))
        if nb:
            miss = sorted(set(miss) | {f"숫자:{x}" for x in nb})
        if miss:
            r["miss"] = miss
            bad.append(r)
        if i % 50 == 0:
            print(f"  {i}/{len(rows)} 검사 ({time.time() - t0:.0f}초)", flush=True)

    print(f"\n어긋난 상품명 {len(bad)}건 / LCP {len({b['lcp_code'] for b in bad})}종",
          flush=True)
    for b in bad[:30]:
        print(f"  {b['l_code']} {b['miss']}", flush=True)
        print(f"      원: {b['own'][:50]}", flush=True)
        print(f"      명: {b['title1']}", flush=True)
    if not args.apply or not bad:
        if bad:
            print("\n--apply 를 주면 다시 만듭니다.", flush=True)
        return

    info = {}
    with db.sqlite_conn() as c:
        for r in c.execute("SELECT lcp_code, brand, maker FROM lcp_product"):
            info[r["lcp_code"]] = (r["brand"] or "", r["maker"] or "")

    ok = fail = 0
    for i, r in enumerate(bad, 1):
        brand, maker = info.get(r["lcp_code"], ("", ""))
        no, L = r["product_no"], r["l_code"]
        try:
            live = attr_detail.fetch_detail(cli.session, no)["title1"].strip()
            if live and live != (r["title1"] or "").strip():
                # 우리가 저장한 값과 다르다 = 사람이 고쳤다. 덮지 않는다
                print(f"  [보호] {L} 사람이 고친 상품명 - 건너뜀", flush=True)
                continue
            page = title_auto.fetch_page(cli.session, no)
            res = title_auto.build_and_check(
                cli.session, page, r["own"], brand=brand, maker=maker,
                cid=str(r.get("etc_category") or ""),
                avoid=title_auto.sibling_words(r["lcp_code"], L),
                log=lambda *_: None)
            if not res["ok"] or not res["title"]:
                fail += 1
                print(f"  !! {L} 최적화 실패", flush=True)
                continue
            still = check(res["title"], r["own"],
                          str(r.get("etc_category") or ""))
            still += [f"숫자:{x}" for x in bad_numbers(
                res["title"], r["own"], str(r.get("etc_category") or ""))]
            if args.risk:
                still = [x for x in still if risky(x)]
            elif not args.all_specs:
                still = [x for x in still if is_qty(x)]
            if still:
                fail += 1
                print(f"  !! {L} 아직 어긋남 {still} : {res['title']}", flush=True)
                continue
            title_auto.save_title(
                cli.session, no, res["title"],
                [x["relKeyword"] for x in res["picked"]], page["shipping"])
            d = attr_detail.fetch_detail(cli.session, no)
            d["lcp_code"] = r["lcp_code"]
            d["l_code"] = L
            db.save_lcode_attr(folder, [d])
            ok += 1
            print(f"  [{i}/{len(bad)}] {L} [{len(res['title'])}자] {res['title']}",
                  flush=True)
        except Exception as e:
            fail += 1
            print(f"  !! {L} {str(e)[:80]}", flush=True)

    print(f"\n다시 만듦 {ok}건 / 실패 {fail}건 ({(time.time() - t0) / 60:.1f}분)",
          flush=True)


if __name__ == "__main__":
    main()
