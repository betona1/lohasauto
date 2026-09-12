"""
ALL 태그 — 폴더 하나의 태그를 끝까지 채운다. (화면의 「🏷 ALL 태그」)

규칙은 `tag_auto` 그대로다.
  · 후보는 **로하스 태그 후보 표에서만** 고른다 (절대규칙 1)
  · 규격·재질·색상·수량·동물·탈것 같은 **상품 특성이 그 L코드 원상품명과
    맞아야** 한다
  · 형제끼리 겹치지 않게 **돌아가며** 나눠 준다
  · **태그 후보가 하나도 없을 때만** 상품명 후보에서 최대 2개 가져온다
    (2026-09-09 사용자 지시)

한 LCP 안에서도 L코드마다 카테고리가 다를 수 있어 **카테고리별로 나눠서**
처리한다. 안 나누면 사탕뽑기 기계의 태그가 기차에도 붙는다
(2026-09-09 LCP_LHA_B907382).

태그 저장은 상품정보 상태를 바꾸지 않는다 — 「저장완료」로 넘어가지 않는다.
"""
import collections

from . import ad_keyword, tabs, tag_auto, title_auto

WANT = 10
FALLBACK = 2          # 태그 후보가 없을 때 상품명 후보에서 가져올 최대 개수


def targets(db, folder: str, todo_only: bool = True,
            any_image: bool = True) -> "collections.OrderedDict":
    """(LCP, 카테고리) 단위로 묶는다. 카테고리가 다르면 다른 묶음이다."""
    sql = ("SELECT a.lcp_code,a.l_code,a.product_no,a.etc_category "
           "FROM lcode_attr a JOIN lcp_lcode l ON l.product_no=a.product_no "
           "WHERE a.folder_name=? AND a.cat_saved=1")
    if todo_only:
        sql += " AND l.info_status='미작업'"
    if not any_image:
        sql += " AND l.img_status='이미지승인완료'"
    sql += " ORDER BY a.lcp_code, a.etc_category, a.l_code"
    out = collections.OrderedDict()
    with db.sqlite_conn() as c:
        for r in c.execute(sql, (folder,)):
            key = (r["lcp_code"], str(r["etc_category"] or ""))
            out.setdefault(key, []).append(dict(r))
    return out


def save_local(db, product_no, tags: list):
    """
    저장한 태그를 로컬 DB(`lcode_attr.tags`)에도 적어 둔다.

    안 적어두면 '놓친 검색어' 보고서가 **방금 넣은 태그를 못 보고** 계속
    없다고 한다 — 사이트엔 들어갔는데 로그엔 그대로 남았다(2026-09-12).
    """
    try:
        with db.sqlite_conn() as c:
            c.execute("UPDATE lcode_attr SET tags=?, tag_count=?"
                      " WHERE product_no=?",
                      (",".join(tags), len(tags), str(product_no)))
    except Exception:
        pass


def plan_group(session, db, rows: list, cid: str, want: int = WANT) -> dict:
    """한 묶음의 태그 계획. 저장은 하지 않는다."""
    names = tag_auto.child_names(session, rows)
    common = tag_auto.head_words(names, cid)
    own = tag_auto.own_words(rows[0]["lcp_code"])
    raw = tabs.fetch_tag_rows(session, rows[0]["product_no"])
    pool = tag_auto._pool(raw, set(), own, tag_auto.brand_vocab(), [],
                          kin=own + " " + " ".join(names), common=common,
                          rules=tag_auto.rules_for(cid),
                          cat_name=tag_auto.cat_name_of(cid))
    ordered = tag_auto._order(pool)
    # **실제로 돈이 나간 검색어를 앞으로 당긴다.** 고르는 대상은 그대로
    # 로하스 후보 표다 - 순서만 바뀐다(절대규칙 유지, 2026-09-12).
    perf = ad_keyword.by_lcp(rows[0]["lcp_code"]) \
        or ad_keyword.by_category(cid)
    if perf:
        ordered = ad_keyword.boost(ordered, perf)
    src, use_want = "태그", want
    if not ordered:
        # 예외 경로 — 태그 후보가 아예 없을 때만 상품명 후보에서
        src, use_want = "상품명후보", FALLBACK
        pg = title_auto.fetch_page(session, rows[0]["product_no"])
        cand = [{"name": d.get("relKeyword"), "views": title_auto.views_of(d),
                 "prio": 2, "kin": True}
                for d in pg["candidates"] if d.get("relKeyword")]
        ordered = tag_auto._order(
            [c for c in cand if tag_auto.usable_tag(c["name"])])[:20]

    used = collections.Counter()
    plan = []
    for r, mine in zip(rows, names):
        try:
            cur = [t["text"] for t in tabs.fetch_saved_tags(
                session, r["product_no"])]
        except Exception:
            cur = []
        sp = tag_auto.specs_of(mine)
        keep = [t for t in cur
                if tag_auto.spec_ok(t, sp)
                and tag_auto.modifier_ok(t, mine, common)]
        got = {x.upper() for x in keep}
        for t in keep:
            used[t.upper()] += 1
        # **실적이 있는 말은 형제끼리 돌리지 않는다.** 그 LCP 가 그 검색어로
        # 돈을 벌었으면 형제 전부에 붙는 게 맞다 - 한 명에게만 주고 나면
        # 나머지는 그 말을 영영 못 쓴다(2026-09-12).
        cands = sorted(ordered,
                       key=lambda c: (0 if c.get("ad_clk") else 1,
                                      -(c.get("ad_cost") or 0),
                                      used[c["name"].upper()],
                                      ordered.index(c)))
        # 자리가 다 찼는데 실적 있는 말이 밖에 있으면, **실적 없는 태그를
        # 빼고 자리를 내준다.** 안 그러면 기존 10개가 그대로 남아 새 말이
        # 영영 못 들어간다 - 61개 중 9개만 반영됐던 이유다.
        if perf and len(keep) >= use_want:
            want_in = [c for c in cands
                       if c.get("ad_clk")
                       and c["name"].upper() not in got
                       and tag_auto.spec_ok(c["name"], sp)
                       and tag_auto.modifier_ok(c["name"], mine, common)]
            if want_in:
                weak = [t for t in keep
                        if not any(c.get("ad_clk") and c["name"].upper() == t.upper()
                                   for c in ordered)]
                for _ in range(min(len(want_in), len(weak))):
                    keep.remove(weak.pop())
                got = {x.upper() for x in keep}
        for c in cands:
            if len(keep) >= use_want:
                break
            nm = c["name"]
            if nm.upper() in got or not tag_auto.spec_ok(nm, sp):
                continue
            if not tag_auto.modifier_ok(nm, mine, common):
                continue
            keep.append(nm)
            got.add(nm.upper())
            used[nm.upper()] += 1
        if keep and keep != cur and len(keep) >= len(cur):
            plan.append({**r, "proposed": keep, "before": len(cur),
                         "source": src})
    return {"plan": plan, "raw": len(raw), "pool": len(ordered), "source": src,
            "perf": len(perf),
            "missed": ad_keyword.missed(ordered, perf) if perf else []}


def run(session, db, folder: str, *, want: int = WANT, todo_only: bool = True,
        any_image: bool = True, apply_: bool = False, log=print,
        progress=None, should_stop=None, stat=None) -> dict:
    groups = targets(db, folder, todo_only=todo_only, any_image=any_image)
    n_rows = sum(len(v) for v in groups.values())
    log(f"{folder} · 대상 {n_rows:,}건 / {len(groups):,}묶음")
    ok = fail = 0
    empty = []
    for i, ((lcp, cid), rows) in enumerate(groups.items(), 1):
        if should_stop and should_stop():
            log("사용자 중단")
            break
        try:
            res = plan_group(session, db, rows, cid, want=want)
        except Exception as e:
            log(f"[{i}/{len(groups)}] {lcp} !! {str(e)[:70]}")
            continue
        if not res["pool"]:
            empty.append(lcp)
            log(f"[{i}/{len(groups)}] {lcp} - 쓸 후보가 없습니다")
        else:
            log(f"[{i}/{len(groups)}] {lcp}  후보 {res['raw']}->{res['pool']}"
                f" ({res['source']})  바꿀 것 {len(res['plan'])}/{len(rows)}건"
                + (f"  · 실적검색어 {res['perf']}개 반영" if res.get("perf")
                   else ""))
            # 후보에 없는데 돈은 나간 검색어 — **넣지 않고 알리기만** 한다.
            # 후보 표 밖의 말을 태그로 넣는 것은 절대규칙 위반이다.
            for m in (res.get("missed") or [])[:3]:
                log(f"      ↳ 놓친 검색어 '{m['query']}' "
                    f"{m['cost']:,}원 · {m['clk']}클릭 (후보에 없음)")
        if apply_:
            for p in res["plan"]:
                try:
                    r = tabs.save_tags(session, p["product_no"], p["proposed"])
                    if r["ok"]:
                        ok += 1
                        tag_auto.log_tag_work(p, p["proposed"], p["source"])
                        save_local(db, p["product_no"], p["proposed"])
                    else:
                        fail += 1
                except Exception as e:
                    fail += 1
                    log(f"      !! {p['l_code']} {str(e)[:60]}")
        if progress:
            progress(i, len(groups))
        if stat:
            stat({"processed": i, "total": len(groups), "ok": ok, "fail": fail,
                  "phase": "태그"})
    return {"ok": ok, "fail": fail, "groups": len(groups), "rows": n_rows,
            "empty": empty}
