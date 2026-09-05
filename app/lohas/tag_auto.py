"""
태그 자동 투입.

태그의 출처는 **로하스 태그 탭의 기본 태그 후보**다. 그 표는 그 상품에
저장된 카테고리를 기준으로 사이트가 만들어 준 것이라 상품과 맞다.

  1순위  태그 탭 후보 (`tbody-tag`)          — 여기서만 고른다. 최소 1개
  대체   태그 후보가 하나도 없을 때만
         상품명 탭 후보에서 **1개만** 가져온다

데이터랩은 태그 소스가 아니다. 카테고리(cid)별 인기키워드와 조회수를 모아
`datalab_keyword` 에 쌓는 용도다. 카테고리가 틀리면 로하스 후보도 틀리므로
카테고리를 먼저 바로잡아야 한다 — 2026-09-05 모형 CCTV 상품이 'CCTV' 로
잡혀 있어 후보가 전부 진짜 CCTV 였다.

선택 순서
  1) 조회수 1000 미만 먼저 (권고라 하드 컷이 아니다 — 모자라면 그 이상도)
  2) 그 안에서 태그사전+추천(prio 3) -> 추천(2) -> 태그사전(1)
  3) 같은 등급에서는 조회수가 높은 것부터

상품별 분포 — 한 LCP 의 L코드에 같은 태그만 넣으면 그 LCP 가 가져가는
검색어가 10개로 끝난다. 후보가 넉넉하면 L코드들에 나눠 준다(`distribute`).

타사 브랜드·안 맞는 기능은 규칙으로 못 거른다. `lcp_product.brand/maker`
사전으로는 '하츠'(사전엔 '바이하츠' 만 있다)를 못 잡고 '국산' 같은 일반어는
오탐이 났다. 그 판단은 `gemini.filter_tags()` 에 맡긴다(선택).
"""
from .. import db
from . import keywords, tabs

MAX_TAGS = tabs.MAX_TAGS       # 사이트 상한 10
LOW_VIEWS = 1000               # 로하스 지침 — 이 미만을 우선한다
TITLE_FALLBACK = 1             # 태그 후보가 0일 때 상품명에서 가져올 개수
MIN_SHARE = 3                  # 상품당 이만큼은 줄 수 있을 때만 나눈다

# 브랜드 칸에 브랜드가 아닌 값이 많이 들어와 있다. 그대로 사전으로 쓰면
# '국산주방수전' 이 '국산' 때문에 걸리는 식으로 오탐이 난다 (2026-09-05 실측).
_NOT_BRAND = {
    "상세페이지참조", "상세설명참조", "상세참조", "자체제작", "자체", "본사",
    "기타", "없음", "해당없음", "국산", "국내", "중국", "해외몰", "휴대용",
    "건식", "하나", "문화", "플랜", "아크", "비트", "매표", "진성", "영동",
    "OEM", "ETC", "UNK", "제작", "수입", "직수입", "협력사", "미표기",
}
_BRAND_MIN = 3                 # 두 글자 이하는 일반어와 겹쳐 오탐이 많다


def brand_vocab() -> set:
    """
    DB 에 등록된 브랜드·제조사 이름 모음.

    태그 후보 표에는 다른 회사 제품명이 섞여 온다. 다만 이 사전만으로는
    부족하다 — 실제 타사 브랜드가 사전에 없을 수 있다. 보조 수단이다.
    """
    out = set()
    with db.sqlite_conn() as c:
        for col in ("brand", "maker"):
            for r in c.execute(
                    f"SELECT DISTINCT {col} v FROM lcp_product "
                    f"WHERE {col} IS NOT NULL AND {col} != ''"):
                v = (r["v"] or "").strip()
                if len(v) < _BRAND_MIN or v in _NOT_BRAND:
                    continue
                # '(주)아트사인' -> '아트사인' 처럼 법인 표기를 떼고도 담는다
                for form in {v, v.replace("(주)", "").replace("주식회사", "")
                             .replace("주", "").strip()}:
                    if len(form) >= _BRAND_MIN and form not in _NOT_BRAND:
                        out.add(form.upper())
    return out


def foreign_brand(name: str, own: str, vocab: set) -> str:
    """
    이 상품 것이 아닌 브랜드가 키워드에 들어 있으면 그 브랜드를 돌려준다.

    own 에는 이 상품의 브랜드·제조사·상품명을 넣는다. 상품명에 들어 있는
    이름은 이 상품 것이므로 통과시킨다.
    """
    up = (name or "").upper()
    own_up = (own or "").upper()
    for b in vocab:
        if b in up and b not in own_up:
            return b
    return ""


def own_words(lcp_code: str) -> str:
    """이 상품의 상품명·브랜드·제조사. 여기 들어간 이름은 통과시킨다."""
    with db.sqlite_conn() as c:
        r = c.execute("SELECT product_name, brand, maker FROM lcp_product "
                      "WHERE lcp_code = ?", (lcp_code,)).fetchone()
    if not r:
        return ""
    return " ".join(x for x in (r["product_name"], r["brand"], r["maker"]) if x)


def _pool(rows: list, exclude: set, own: str = "", vocab: set = None,
          dropped: list = None) -> list:
    """후보 행에서 쓸 수 있는 것만 남긴다."""
    out = []
    for r in rows:
        name = (r.get("name") or "").strip()
        if not name or name.upper() in exclude:
            continue
        if r.get("banned"):                 # 금지어 열이 채워진 행
            continue
        if not keywords.usable(name):       # 금지어 사전 / 숫자 · 색상 · 갯수
            continue
        if vocab:
            b = foreign_brand(name, own, vocab)
            if b:
                if dropped is not None:
                    dropped.append(f"{name}({b})")
                continue
        out.append({"name": name,
                    "views": int(r.get("views") or 0),
                    "prio": int(r.get("prio") or 0)})
    return out


def _order(cands: list) -> list:
    """1000 미만 먼저 / 태그사전+추천 -> 추천 -> 태그사전 / 조회수 높은 순."""
    low = [c for c in cands if c["views"] < LOW_VIEWS]
    high = [c for c in cands if c["views"] >= LOW_VIEWS]

    def key(c):
        return (-(c["prio"] or 0), -(c["views"] or 0))

    return sorted(low, key=key) + sorted(high, key=key)


def distribute(pool: list, n_targets: int, want: int = MAX_TAGS) -> list:
    """
    정렬된 후보를 상품 수만큼 나눈다.

    나누는 게 늘 이득은 아니다. 후보가 상품 수보다 적으면 상품마다 1개씩만
    돌아가 오히려 나빠진다 — 쌀 상품(후보 4개 / L코드 20건)을 나눠봤더니
    20건이 태그 1개씩만 받았다(2026-09-05). 그래서 **상품당 최소 MIN_SHARE
    개는 줄 수 있을 때만** 나누고, 아니면 모두에게 같은 상위 목록을 준다.

        후보 48 / 상품  7  ->  나눔. 건당 7개, 중복 없음
        후보  4 / 상품 20  ->  안 나눔. 20건 모두 같은 4개

    나눌 때는 돌아가며 하나씩 집어 주므로 각 상품이 상위·하위를 고루 받는다.
    """
    if n_targets <= 0:
        return []
    if not pool:
        return [[] for _ in range(n_targets)]

    if len(pool) < n_targets * MIN_SHARE:
        share = pool[:want]
        return [list(share) for _ in range(n_targets)]

    out = [[] for _ in range(n_targets)]
    per = min(want, max(1, -(-len(pool) // n_targets)))   # 올림 나눗셈
    i = 0
    for c in pool:
        for _ in range(n_targets):                 # 자리가 빈 상품을 찾는다
            slot = out[i % n_targets]
            i += 1
            if len(slot) < per:
                slot.append(c)
                break
    return out


def ai_filter(lcp_code: str, ordered: list, log=print) -> list:
    """
    타사 브랜드·이 상품에 없는 기능을 AI 가 걸러낸다.

    규칙으로 안 되는 부분만 맡긴다. 실패하거나 전부 빼라고 하면 원래 목록을
    그대로 돌려준다 — 태그가 0개가 되는 쪽이 더 나쁘다.
    """
    from . import gemini

    if not ordered:
        return ordered
    with db.sqlite_conn() as c:
        p = c.execute("SELECT product_name, brand, maker FROM lcp_product "
                      "WHERE lcp_code = ?", (lcp_code,)).fetchone()
    res = gemini.filter_tags(
        (p["product_name"] if p else "") or "",
        [x["name"] for x in ordered],
        (p["brand"] if p else "") or "",
        (p["maker"] if p else "") or "", log=lambda *_: None)
    if not res["ok"] or not res["drop"]:
        return ordered
    bad = set(res["drop"])
    kept = [x for x in ordered if x["name"] not in bad]
    if not kept:
        return ordered
    log(f"  [태그] AI 제외 {len(bad)}개: " + ", ".join(sorted(bad)[:8]))
    return kept


def log_tag_work(row: dict, tags: list, source: str = "태그") -> None:
    """
    태그를 넣은 사실을 task_log 에 남긴다.

    나중에 '무엇을 자동으로 넣었는지' 를 사람이 훑어보려면 기록이 있어야
    한다. 사이트에서 다시 읽어도 사람이 넣은 것과 구분이 안 된다.
    """
    try:
        db.save_task_log({
            "folder_name": db.get_job_folder(),
            "lcp_code": row.get("lcp_code") or "",
            "l_code": row.get("l_code") or "",
            "product_no": str(row.get("product_no") or ""),
            "step": "태그",
            "action": "자동입력",
            "status": "ok",
            "picked": list(tags),
            "source": source,
            "message": "",
        })
    except Exception:
        pass          # 기록 실패가 저장을 막으면 안 된다


def plan_rows(session, rows: list, *, want: int = MAX_TAGS,
              overwrite: bool = False, use_ai: bool = False, log=print,
              should_stop=None) -> dict:
    """
    L코드별로 **넣을 태그를 정하기만** 한다. 저장은 하지 않는다.

    화면에서 사람이 눈으로 보고 고칠 수 있게 하려고 계획과 저장을 갈랐다.
    `save_plan()` 에 그대로 넘기면 저장된다.

    반환 {'rows': [{l_code, product_no, current, proposed, source}],
          'source', 'pool', 'dropped_brand', 'dropped_ai', 'mode'}
    """
    have, out = {}, []
    for r in rows:
        if should_stop and should_stop():
            break
        try:
            have[r["l_code"]] = tabs.fetch_saved_tags(session, r["product_no"])
        except Exception as e:
            have[r["l_code"]] = []
            log(f"  !! {r['l_code']} 조회 실패 {str(e)[:50]}")

    targets = [r for r in rows if overwrite or not have.get(r["l_code"])]
    base = {"rows": [], "source": "", "pool": 0, "dropped_brand": [],
            "dropped_ai": [], "mode": ""}
    for r in rows:                       # 대상이 아니어도 현황은 보여준다
        base["rows"].append({
            "l_code": r["l_code"], "product_no": r["product_no"],
            "current": [t["text"] for t in have.get(r["l_code"], [])],
            "proposed": [], "source": ""})
    if not targets:
        return base

    used = set()
    if not overwrite:
        for ts in have.values():
            for t in ts:
                used.add(t["text"].upper())

    head = targets[0]
    lcp = head.get("lcp_code") or ""
    own = own_words(lcp)
    vocab = brand_vocab()
    drop_brand = []

    pool = _pool(tabs.fetch_tag_rows(session, head["product_no"]),
                 used, own, vocab, drop_brand)
    source = "태그"
    if not pool:
        pool = _pool(tabs.fetch_title_rows(session, head["product_no"], 1),
                     used, own, vocab, drop_brand)
        source = "상품명"
        want = TITLE_FALLBACK
    if not pool:
        base["dropped_brand"] = drop_brand
        return base

    ordered = _order(pool)
    n_before = len(ordered)
    drop_ai = []
    if use_ai and source == "태그":
        kept = ai_filter(lcp, ordered, log=log)
        drop_ai = [c["name"] for c in ordered
                   if c["name"] not in {k["name"] for k in kept}]
        ordered = kept

    shares = distribute(ordered, len(targets), want)
    by_l = {r["l_code"]: s for r, s in zip(targets, shares)}
    for row in base["rows"]:
        share = by_l.get(row["l_code"])
        if share:
            row["proposed"] = [c["name"] for c in share]
            row["source"] = source
    base.update({"source": source, "pool": n_before,
                 "dropped_brand": drop_brand, "dropped_ai": drop_ai,
                 "mode": "분배" if len(shares[0]) < len(ordered) else "동일"})
    return base


def save_plan(session, plan_rows_: list, *, log=print, should_stop=None,
              progress=None) -> dict:
    """
    `plan_rows()` 결과(또는 사람이 화면에서 고친 것)를 저장한다.

    태그 문자열만 있으면 되고, 검색코드는 여기서 tag_search 로 받는다.
    """
    ok = fail = skip = 0
    saved = []
    todo = [r for r in plan_rows_ if r.get("proposed")]
    for i, r in enumerate(todo, 1):
        if should_stop and should_stop():
            log("[태그] 사용자 중단")
            break
        try:
            res = tabs.tag_search(session, r["product_no"], r["proposed"])
            codes = {t["text"].upper(): t["code"] for t in res["ok"]}
            for t in res["x"]:
                codes.setdefault(t["text"].upper(), -1)
            bad = {t["text"].upper() for t in res["restricted"]}
            payload = [{"text": n, "code": codes.get(n.upper(), -1)}
                       for n in r["proposed"] if n.upper() not in bad]
            if not payload:
                skip += 1
                log(f"  - {r['l_code']} 등록 가능한 태그가 없습니다")
                continue
            tabs.save_tags(session, r["product_no"], payload)
            got = tabs.fetch_saved_tags(session, r["product_no"])
            if len(got) == len(payload):
                ok += 1
                saved.append(r)
                log_tag_work(r, [t["text"] for t in payload],
                             r.get("source") or "태그")
                log(f"  + {r['l_code']} " + ", ".join(t["text"] for t in payload))
            else:
                fail += 1
                log(f"  !! {r['l_code']} 저장 {len(got)}/{len(payload)}개")
        except Exception as e:
            fail += 1
            log(f"  !! {r['l_code']} {str(e)[:70]}")
        if progress:
            progress(i, len(todo))
    return {"ok": ok, "fail": fail, "skip": skip, "saved": saved}


def apply_to_rows(session, rows: list, *, want: int = MAX_TAGS,
                  overwrite: bool = False, use_ai: bool = False,
                  log=print, should_stop=None, progress=None) -> dict:
    """
    한 LCP 의 L코드들에 태그를 넣는다.

    후보는 그 LCP 의 대표 한 건에서 읽는다. 같은 LCP 는 색·크기만 다른
    같은 상품이라 후보 표가 같다. 사람이 이미 달아둔 태그는 후보에서 빼고
    남은 것을 빈 L코드들에 나눠 준다.
    """
    ok = fail = skip = 0
    saved, picks = [], {}

    # 지금 상태를 먼저 읽는다. 사람이 손으로 넣어둔 것을 덮지 않기 위해서다.
    have = {}
    for r in rows:
        try:
            have[r["l_code"]] = tabs.fetch_saved_tags(session, r["product_no"])
        except Exception as e:
            have[r["l_code"]] = []
            log(f"  !! {r['l_code']} 조회 실패 {str(e)[:50]}")

    targets = [r for r in rows if overwrite or not have[r["l_code"]]]
    skip += len(rows) - len(targets)
    if not targets:
        return {"ok": 0, "fail": 0, "skip": skip, "saved": [], "picks": {}}

    # 형제가 이미 쓰고 있는 태그는 빼고 나눈다 (덮어쓰기면 전부 다시 나눈다).
    used = set()
    if not overwrite:
        for ts in have.values():
            for t in ts:
                used.add(t["text"].upper())

    head = targets[0]
    lcp = head.get("lcp_code") or ""
    own = own_words(lcp)
    vocab = brand_vocab()
    drop_brand = []

    pool = _pool(tabs.fetch_tag_rows(session, head["product_no"]),
                 used, own, vocab, drop_brand)
    source = "태그"
    if drop_brand:
        log(f"  [태그] 타사 브랜드 제외 {len(drop_brand)}개: "
            + ", ".join(drop_brand[:6]))
    if not pool:
        # 태그 후보가 하나도 없을 때만 상품명에서 1개를 가져온다.
        pool = _pool(tabs.fetch_title_rows(session, head["product_no"], 1),
                     used, own, vocab, drop_brand)
        source = "상품명"
        want = TITLE_FALLBACK
        log(f"  [태그] 후보 0개 - 상품명에서 {TITLE_FALLBACK}개만 씁니다")
    if not pool:
        log("  - 넣을 태그가 없습니다")
        return {"ok": 0, "fail": 0, "skip": skip + len(targets),
                "saved": [], "picks": {}}

    ordered = _order(pool)
    if use_ai and source == "태그":
        ordered = ai_filter(lcp, ordered, log=log)

    shares = distribute(ordered, len(targets), want)
    mode = "분배" if len(shares[0]) < len(ordered) else "동일"
    log(f"  [태그] {source} 후보 {len(ordered)}개 -> {len(targets)}건에 "
        f"{mode} (건당 {len(shares[0])}개)")

    for i, (r, share) in enumerate(zip(targets, shares), 1):
        if should_stop and should_stop():
            log("[태그] 사용자 중단")
            break
        try:
            res = tabs.tag_search(session, r["product_no"],
                                  [c["name"] for c in share])
            codes = {t["text"].upper(): t["code"] for t in res["ok"]}
            for t in res["x"]:
                codes.setdefault(t["text"].upper(), -1)
            bad = {t["text"].upper() for t in res["restricted"]}

            payload = [{"text": c["name"],
                        "code": codes.get(c["name"].upper(), -1)}
                       for c in share if c["name"].upper() not in bad]
            if not payload:
                # 전부 등록 불가면 상위 후보에서 하나라도 채운다.
                for c in ordered:
                    if c["name"].upper() in bad:
                        continue
                    payload = [{"text": c["name"], "code": -1}]
                    break
            if not payload:
                skip += 1
                log(f"  - {r['l_code']} 등록 가능한 태그가 없습니다")
                continue

            tabs.save_tags(session, r["product_no"], payload)
            got = tabs.fetch_saved_tags(session, r["product_no"])
            if len(got) == len(payload):
                ok += 1
                saved.append(r)
                picks[r["l_code"]] = {"tags": share, "source": source}
                log_tag_work(r, [t["text"] for t in payload], source)
                log(f"  + {r['l_code']} [{source}] "
                    + ", ".join(t["text"] for t in payload))
            else:
                fail += 1
                log(f"  !! {r['l_code']} 저장 {len(got)}/{len(payload)}개")
        except Exception as e:
            fail += 1
            log(f"  !! {r['l_code']} {str(e)[:70]}")
        if progress:
            progress(i, len(targets))

    return {"ok": ok, "fail": fail, "skip": skip, "saved": saved,
            "picks": picks}
