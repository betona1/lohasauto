"""
상품정보 상태를 넘긴다 — 「저장완료」 / 제외 / 보류. (attr 팝업의 그 버튼)

**사용자가 시켰을 때만 쓴다.** 값(카테고리·태그·상품명)을 저장하는 것과
상태를 넘기는 것은 다른 일이고, 넘기는 것은 사람의 확인 단계다
(2026-09-08 사용자: "저장완료 는 내가 확인후 누룰것이야").

    python -X utf8 tools/mark_done.py --lcp LCP_LHA_B915565
    ... --apply                실제로 넘긴다 (기본은 드라이런)
    ... --status exclude       제외 / hold 보류
    ... --only L1234567,L2345678

사이트 버튼과 같은 요청이다.
    POST <attr 팝업 URL>   product_id=<L코드에서 L·선행0 뗀 값>  action_type=save
버튼은 카테고리·속성·상품명이 다 저장돼야 넘겨준다. 안 되어 있으면
사이트가 alert 로 막으므로, 여기서도 미리 보고 건너뛴다.
"""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from app import db                                        # noqa: E402
from app.lohas import category, session as ses, tabs      # noqa: E402

LABEL = {"save": "저장완료", "exclude": "제외", "hold": "보류"}


def arg(name, default=""):
    for i, a in enumerate(sys.argv):
        if a == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main():
    lcp = arg("--lcp")
    status = arg("--status", "save")
    apply_ = "--apply" in sys.argv
    only = {x.strip() for x in arg("--only").split(",") if x.strip()}
    if not lcp:
        print("!! --lcp 를 주십시오"); return 1
    if status not in LABEL:
        print(f"!! --status 는 {'/'.join(LABEL)} 중 하나"); return 1

    with db.sqlite_conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT l_code, product_no, folder_name FROM lcode_attr "
            "WHERE lcp_code=? ORDER BY l_code", (lcp,))]
    if only:
        rows = [r for r in rows if r["l_code"] in only]
    if not rows:
        print("대상이 없습니다"); return 0

    cli = ses.get_client()
    s = cli.session
    folder = rows[0]["folder_name"] or db.get_job_folder()

    now = {}
    for k, v in (("none", "미작업"), ("save", "저장완료"),
                 ("exclude", "제외"), ("hold", "보류")):
        for x in cli.search_full(folder, "all", k)["rows"]:
            now[x[1]] = v

    print(f"{lcp}  {len(rows)}건 -> {LABEL[status]}   (폴더 {folder})")
    ok = skip = fail = 0
    for r in rows:
        cur = now.get(r["l_code"], "?")
        if cur == LABEL[status]:
            skip += 1
            print(f"  = {r['l_code']}  이미 {cur}")
            continue
        # 사이트 버튼과 같은 조건 — 탭이 다 저장돼 있어야 넘어간다
        try:
            tb = tabs.fetch_saved_tabs(s, r["product_no"])
        except Exception:
            tb = {}
        if status == "save" and not (tb.get("tag") and tb.get("product")):
            skip += 1
            print(f"  ! {r['l_code']}  탭 미저장 {tb} - 건너뜀")
            continue
        print(f"  {'+' if apply_ else '·'} {r['l_code']}  {cur} -> "
              f"{LABEL[status]}")
        if not apply_:
            continue
        try:
            res = s.post(tabs.URL_ATTR.format(no=r["product_no"]),
                         data={"product_id": category.product_id_of(r["l_code"]),
                               "action_type": status}, timeout=40)
            ok += res.status_code == 200
            if res.status_code != 200:
                fail += 1
                print(f"      !! HTTP {res.status_code}")
        except Exception as e:
            fail += 1
            print(f"      !! {str(e)[:70]}")

    if not apply_:
        print(f"\n[드라이런] --apply 를 주면 넘깁니다. (건너뜀 {skip}건)")
        return 0

    # 전건 재조회로 확인하고 로컬 DB 도 맞춘다
    after = {}
    for k, v in (("none", "미작업"), ("save", "저장완료"),
                 ("exclude", "제외"), ("hold", "보류")):
        for x in cli.search_full(folder, "all", k)["rows"]:
            after[x[1]] = v
    done = 0
    with db.sqlite_conn() as c:
        for r in rows:
            st = after.get(r["l_code"], "?")
            done += st == LABEL[status]
            c.execute("UPDATE lcp_lcode SET info_status=? WHERE l_code=?",
                      (st, r["l_code"]))
    print(f"\n요청 {ok} / 실패 {fail} / 건너뜀 {skip}")
    print(f"확인 — {LABEL[status]} {done}/{len(rows)}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
