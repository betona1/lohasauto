"""
카테고리 저장 계획 — 어떤 LCP 에 어떤 카테고리를 넣을지와 그 근거.

근거 등급(tier)마다 정확도가 다르다. 사람이 이미 저장해둔 367종을 정답지로
삼아 실측한 값이다(tools/verify_category_rule.py).

    형제    같은 LCP 의 다른 L코드에 사람이 넣어둔 값   — 가장 강한 근거
    압도적  후보 1위가 2위의 3배 이상                    96.8%
    접전    그 외                                        74.1%  <- 사람이 골라야
    수동    용량·단위 입력이 필요하거나, 형제 카테고리가 이미 갈린 경우

'형제'와 '압도적'은 자동 저장했고, 남는 건 '접전'과 '수동'이다.
그 둘이 검토 화면(CategoryReviewPage)의 대상이다.
"""
from . import category

TIER_SIBLING = "형제"
TIER_DOMINANT = "압도적"
TIER_CLOSE = "접전"
TIER_UNIT = "용량"
TIER_SPLIT = "갈림"

AUTO_TIERS = (TIER_SIBLING, TIER_DOMINANT)
TIER_NOTE = {
    TIER_SIBLING: "같은 LCP 의 다른 L코드가 쓰는 값",
    TIER_DOMINANT: "1위가 2위의 3배 이상 (실측 96.8%)",
    TIER_CLOSE: "1·2위가 접전 (실측 74.1%) — 확인 필요",
    TIER_UNIT: "용량·단위 입력이 필요",
    TIER_SPLIT: "형제 L코드끼리 카테고리가 갈림",
}


def pending(db, folder_name: str = None, only: str = "") -> dict:
    """카테고리 미저장 L코드를 LCP 단위로 묶는다."""
    sql = ("select lcp_code, l_code, product_no, etc_category "
           "from lcode_attr where next_step='카테고리'")
    args = []
    if folder_name:
        sql += " and folder_name=?"
        args.append(folder_name)
    if only:
        sql += " and lcp_code=?"
        args.append(only)
    sql += " order by lcp_code, l_code"
    groups = {}
    with db.sqlite_conn() as c:
        for r in c.execute(sql, args):
            groups.setdefault(r["lcp_code"], []).append(dict(r))
    return groups


def siblings(db) -> dict:
    """LCP -> {이미 저장된 카테고리 코드: L코드 수}."""
    out = {}
    with db.sqlite_conn() as c:
        for r in c.execute(
                "select lcp_code, etc_category, count(*) n from lcode_attr "
                "where cat_saved=1 group by lcp_code, etc_category"):
            out.setdefault(r["lcp_code"], {})[str(r["etc_category"])] = r["n"]
    return out


def decide(cands: list, sib: dict) -> dict:
    """
    후보 목록과 형제 정보로 한 LCP 의 추천값과 근거 등급을 정한다.
    cands 는 서버가 주는 순서 그대로(건수 내림차순)다.
    """
    if not cands:
        return {}
    # 형제가 한 가지로 통일돼 있으면 그대로 따른다. 갈려 있으면 사람이 정해야 한다.
    split = len(sib) > 1
    prefer = next(iter(sib)) if len(sib) == 1 else ""
    p = category.pick(cands, prefer=prefer)
    p["cnt2"] = int(str((cands[1].get("cnt") if len(cands) > 1 else 0) or 0) or 0)
    p["sibling"] = dict(sib)
    p["from_sibling"] = bool(prefer) and p["code"] == prefer

    if split:
        tier = TIER_SPLIT
    elif p["needs_manual"]:
        tier = TIER_UNIT
    elif p["from_sibling"]:
        tier = TIER_SIBLING
    elif p["cnt"] >= 3 * max(1, p["cnt2"]):
        tier = TIER_DOMINANT
    else:
        tier = TIER_CLOSE

    p["tier"] = tier
    p["auto"] = tier in AUTO_TIERS
    p["note"] = TIER_NOTE[tier]
    return p


def build(session, db, folder_name: str = None, only: str = "",
          tiers=(), log=print, progress=None, should_stop=None) -> list:
    """
    검토·저장 계획을 만든다. 후보 조회는 읽기 전용이라 안전하다.
    tiers 를 주면 그 등급만 남긴다.
    """
    groups = pending(db, folder_name, only)
    sibs = siblings(db)
    names = list(groups)
    out = []

    for i, lcp in enumerate(names, 1):
        if should_stop and should_stop():
            log("[계획] 사용자 중단")
            break
        rows = groups[lcp]
        try:
            cands = category.fetch_candidates(session, lcp)
        except Exception as e:
            out.append({"lcp_code": lcp, "rows": rows, "tier": "실패",
                        "note": str(e)[:80], "candidates": [], "auto": False})
            continue
        p = decide(cands, sibs.get(lcp) or {})
        if not p:
            out.append({"lcp_code": lcp, "rows": rows, "tier": "후보없음",
                        "note": "서버가 후보를 주지 않음", "candidates": [],
                        "auto": False})
            continue
        if tiers and p["tier"] not in tiers:
            continue
        out.append({**p, "lcp_code": lcp, "rows": rows, "candidates": cands})
        if progress:
            progress(i, len(names))

    return out


def save_group(session, item: dict, code: str, *, capacity="", unit="",
               total_capacity="", log=print) -> dict:
    """
    한 LCP 의 L코드 전부에 카테고리를 저장한다.
    code 는 화면에서 사람이 고른 값이 우선이고, 없으면 추천값을 쓴다.
    """
    ok, fail, saved = 0, 0, []
    for r in item["rows"]:
        try:
            res = category.save_category(
                session, r["product_no"], r["l_code"], code,
                capacity=capacity, unit=unit, total_capacity=total_capacity,
                current=r.get("etc_category") or "")
            if res["ok"]:
                ok += 1
                saved.append(r)
            else:
                fail += 1
                log(f"  !! {r['l_code']} {res['message'][:50]}")
        except Exception as e:
            fail += 1
            log(f"  !! {r['l_code']} {str(e)[:70]}")
    return {"ok": ok, "fail": fail, "saved": saved}

def auto_choice(item: dict, use_ai: bool = True) -> dict:
    """
    'ALL 카테고리' 용 — 등급을 가리지 않고 한 가지 코드를 정한다.

    등급별로 근거가 다르므로 고르는 방법도 다르다.
      형제·압도적  규칙 그대로 (실측 96.8% 이상)
      접전        AI 판단이 있으면 그것, 없으면 규칙 (규칙만 쓰면 74.1%)
      갈림        형제 L코드가 가장 많이 쓰는 카테고리
      용량        규칙 + 후보가 주는 capacity/unit. 총 용량은 비운다
                  (비워도 저장된다 - L0000335 로 실측)

    반환값은 CategorySaveWorker 의 job 에 그대로 들어간다.
    """
    cands = item.get("candidates") or []
    if not cands:
        return {}
    tier = item.get("tier")
    code, src = str(item.get("code") or ""), "규칙"

    if tier == TIER_SPLIT and item.get("sibling"):
        # 갈려 있어도 다수가 쓰는 쪽이 가장 나은 근거다.
        code = max(item["sibling"].items(), key=lambda kv: kv[1])[0]
        src = "형제최다"
    elif tier == TIER_CLOSE and use_ai and (item.get("ai") or {}).get("code"):
        code = str(item["ai"]["code"])
        src = "AI"

    c = next((x for x in cands if str(x.get("code")) == code), None)
    if c is None:                     # 후보에 없는 코드는 못 쓴다
        c = cands[0]
        code, src = str(c.get("code") or ""), "규칙(대체)"
    return {"code": code, "name": c.get("name") or "",
            "capacity": c.get("capacity") or "", "unit": c.get("unit") or "",
            "total_capacity": "", "source": src, "tier": tier}
