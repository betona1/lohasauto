"""
로하스 태그/상품명 탭 키워드 수집.

로하스는 LCP 에 잡힌 카테고리를 기준으로 후보 키워드를 만들어 내려준다. 그
표를 그대로 긁어 **태그용 / 상품명용으로 구분해** 쌓아두면, 같은 카테고리의
다른 상품을 작업할 때 그대로 쓸 수 있는 사전이 된다.

  태그      /commercial_ss_tab_tag/popup/ok/no/{no}       -> tbody-tag
  상품명N   /commercial_ss_tab_title(N)/popup/ok/...      -> tbody-title

표에는 키워드 말고도 **금지어 분류·사용여부·조회수·(태그사전)/(추천) 라벨**이
같이 들어있다. 전부 저장한다 — 나중에 어느 조건으로 거를지 바뀔 수 있어서다.

전제: **카테고리가 저장된 LCP 만 가능하다.** 카테고리가 없으면 서버가 표를
만들지 않고 500 을 준다(CLAUDE.md 3번 (A)). 그래서 대상은 `cat_saved=1` 인
상품 하나(LCP 대표)로 잡는다. 같은 LCP 안에서는 후보가 같게 나온다.
"""
import time

from . import tabs


def collect_lcp(session, lcp_code: str, product_no: str, cid: str = "",
                titles: int = 1, timeout: int = 60) -> list:
    """
    LCP 대표 상품 하나에서 태그·상품명 후보를 긁는다.
    titles 는 상품명 탭을 몇 번까지 볼지 (1이면 상품명1만).
    """
    out = []

    for r in tabs.fetch_tag_rows(session, product_no, timeout=timeout):
        out.append(_row(r, lcp_code, product_no, cid, "tag", 0))

    for n in range(1, max(1, titles) + 1):
        try:
            rows = tabs.fetch_title_rows(session, product_no, n=n,
                                         timeout=timeout)
        except Exception:
            break
        if not rows:
            break
        for r in rows:
            out.append(_row(r, lcp_code, product_no, cid, "title", n))

    return out


def _row(r: dict, lcp_code, product_no, cid, kind, title_no) -> dict:
    return {
        "cid": cid, "lcp_code": lcp_code, "product_no": str(product_no),
        "kind": kind, "title_no": title_no,
        "keyword": r.get("name") or "",
        "views": r.get("views") or 0,
        "banned": r.get("banned") or "",
        "used": r.get("used") or "",
        "prio": r.get("prio") or 0,
        "is_dict": bool(r.get("dict")),
        "is_rec": bool(r.get("rec")),
    }


def targets(db, folder_name: str = None, redo: bool = False,
            only: str = "") -> list:
    """
    수집 대상 = 카테고리가 저장된 LCP 하나당 상품 1건.
    카테고리가 없는 LCP 는 애초에 표가 안 나오므로 제외한다.
    """
    sql = ("SELECT lcp_code, MIN(product_no) product_no, "
           "MIN(etc_category) cid FROM lcode_attr "
           "WHERE cat_saved = 1 AND etc_category <> ''")
    args = []
    if folder_name:
        sql += " AND folder_name = ?"
        args.append(folder_name)
    if only:
        sql += " AND lcp_code = ?"
        args.append(only)
    sql += " GROUP BY lcp_code ORDER BY lcp_code"
    with db.sqlite_conn() as c:
        rows = [dict(r) for r in c.execute(sql, args).fetchall()]
    if redo:
        return rows
    have = db.cat_keyword_lcps()
    return [r for r in rows if r["lcp_code"] not in have]


def collect_folder(db, session, rows: list, titles: int = 1, log=print,
                   progress=None, should_stop=None, delay: float = 0.0) -> dict:
    """대상 LCP 들을 돌며 긁고 바로 저장한다."""
    t0 = time.time()
    ok = fail = empty = 0
    n_tag = n_title = 0
    total = len(rows)

    for i, r in enumerate(rows, 1):
        if should_stop and should_stop():
            log("[키워드] 사용자 중단")
            break
        lcp = r["lcp_code"]
        try:
            got = collect_lcp(session, lcp, r["product_no"],
                              str(r.get("cid") or ""), titles=titles)
        except Exception as e:
            fail += 1
            if fail <= 5:
                log(f"  ! {lcp} 실패: {str(e)[:70]}")
            continue
        if not got:
            empty += 1
            continue
        st = db.save_cat_keywords(lcp, got)
        ok += 1
        t = sum(1 for x in got if x["kind"] == "tag")
        n_tag += t
        n_title += len(got) - t
        if i % 25 == 0 or total <= 25:
            log(f"  [{i}/{total}] {lcp}  태그 {t} / 상품명 {len(got) - t}"
                f"  (누적 {n_tag + n_title:,}개)")
        if progress:
            progress(i, total)
        if delay:
            time.sleep(delay)

    el = round(time.time() - t0, 1)
    log(f"[키워드] 완료 {ok:,}종 / 빈표 {empty} / 실패 {fail} ({el}초)")
    log(f"[키워드] 태그 {n_tag:,}개 · 상품명 {n_title:,}개")
    return {"ok": ok, "fail": fail, "empty": empty, "tag": n_tag,
            "title": n_title, "elapsed_sec": el}
