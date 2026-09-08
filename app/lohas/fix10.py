"""
수정사항 1.0 일괄수정 — 브라우저 없이 HTTP 로.

로하스 「광고상품관리」 목록에서 **정보수정** 표시가 붙은 상품을 하나씩 열어
1.0 수정 마법사를 끝까지 눌러주는 일이다. 사람이 하던 것과 같은 순서다.

    목록(commercial_tiedlist, info_update=1)
      -> [1.0수정] editProduct6  =  commercial_prodtie2Edit1   (1단계: 묶음 확인)
      -> [다 음]   next()        =  commercial_prodtie2Edit2   (2단계: 내용 확인)
      -> [저장 후 닫기]          =  commercial_prodtie2Edit3   (저장)

`LOHASPIC/lohaspicps6.py` 의 `_run_edit_core` 는 이 과정을 Selenium 으로
클릭했다. 여기서는 폼을 그대로 재현해 POST 한다 — 브라우저가 없으니
**정방향·역방향을 동시에** 돌릴 수 있고 백그라운드로도 돈다.

정·역 동시 진행은 목록의 앞뒤에서 서로 마주 보고 좁혀 오는 방식이다.
같은 상품을 두 번 하지 않도록 처리한 product_id 를 공유한다.

전상품품절: 묶인 상품이 전부 품절이면 사이트가 저장 단계에서 막는다.
그런 LCP 는 시도하지 않고 `soldout` 으로 기록만 남긴다(사용자 지침 2026-09-05).
"""
import html as _html
import json
import re
import threading
import time
import uuid

from .. import db

SITE = "http://com.exponet.co.kr"
BASE = SITE + "/manager/commercial"
URL_LIST = BASE + "/commercial_tiedlist"
URL_EDIT1 = (BASE + "/commercial_prodtie2Edit1/popup/ok"
             "/product_id/{pid}/commercial_type/{ct}")
# 1단계의 [다 음] 이 가는 곳. price_cal=1(계산 안함) · is_added=N 이면 여기다
URL_STEP1 = (BASE + "/commercial_prodtie2Edit2/popup/ok"
             "/product_id/{pid}/commercial_type/{ct}"
             "/order1/date_add/sort1/desc/order2//sort2//order3//sort3"
             "/order4//sort4/")

# 묶인 상품이 이 상태면 품절로 본다 (사이트 JS 와 같은 판정)
SOLDOUT_STATES = ("soldout", "keepsoldout")

_RE_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_RE_PID = re.compile(r'name="product_id\[\]"\s+value="(\d+)"')
_RE_CODE = re.compile(r"(LCP_[A-Z0-9]+_[A-Z0-9]+)")
_RE_FIX10 = re.compile(r"editProduct6\((\d+)")
_RE_TIED = re.compile(r"var\s+tiedProducts\s*=\s*(\[.*?\]);\s*\n", re.S)
_RE_DEFID = re.compile(r"var\s+defaultId\s*=\s*(\d+)")
_RE_IMGT3 = re.compile(r"var\s+img_turn3\s*=\s*'([^']*)'")
_RE_ALERT = re.compile(r"alert\('((?:[^'\\]|\\.)*)'\)")


# ---------------------------------------------------------------- 목록

def _search_data(folder: str, asc: bool, viewnum: int) -> dict:
    """검색 폼(searchForm)이 보내는 값. 화면에서 고르는 것과 같다."""
    return {
        "order1": "product_code", "sort1": "asc" if asc else "desc",
        "order2": "", "sort2": "asc", "order3": "", "sort3": "asc",
        "order4": "", "sort4": "asc",
        "site_categoryname_search": folder,
        "info_update": "1",              # 수정사항유
        "commercial_type": "B",
        "viewnum": str(viewnum),
        "compare": "complete",
        "state": "", "esm2_soldout": "", "esm_use": "",
        "fc": "", "fv": "", "date_type": "", "sdate": "", "edate": "",
        "uselevel": "", "site_shipfee": "",
        "site_categoryname": "", "site_categoryname2": "",
        "category_market": "",
    }


def parse_rows(html_text: str) -> list:
    """
    결과 표에서 한 줄씩 뽑는다.

    [1.0수정] 버튼(editProduct6)이 있는 줄만 대상이다.
    """
    i = html_text.find('class="grid_tbl"')
    if i < 0:
        return []
    out = []
    for m in _RE_ROW.finditer(html_text[i:]):
        row = m.group(1)
        pid = _RE_PID.search(row)
        fix = _RE_FIX10.search(row)
        if not pid or not fix:
            continue
        code = _RE_CODE.search(row)
        # 상품명 칸: 코드 다음 td 의 첫 줄
        name = ""
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(tds) >= 5:
            txt = re.sub(r"<[^>]+>", " ", tds[4].split("<br>")[0])
            name = " ".join(_html.unescape(txt).split())[:80]
        state = ""
        if len(tds) >= 9:
            state = " ".join(re.sub(r"<[^>]+>", " ", tds[8]).split())
        out.append({"product_id": pid.group(1),
                    "product_code": code.group(1) if code else "",
                    "title": name, "state": state})
    return out


def search(session, folder: str, asc: bool = True, viewnum: int = 500,
           pages: int = 20, timeout: int = 180) -> list:
    """
    그 폴더에서 **수정사항이 있는** 광고상품을 모두 가져온다.

    한 페이지 최대 500개라 넘치면 다음 쪽을 이어 읽는다.
    """
    rows, seen = [], set()
    data = _search_data(folder, asc, viewnum)
    for p in range(1, pages + 1):
        url = URL_LIST if p == 1 else f"{URL_LIST}/p/{p}"
        r = session.post(url, data=data, timeout=timeout)
        r.encoding = "utf-8"
        got = parse_rows(r.text)
        fresh = [x for x in got if x["product_id"] not in seen]
        for x in fresh:
            seen.add(x["product_id"])
        rows += fresh
        if len(got) < viewnum or not fresh:
            break
    return rows


SETTING_SOLDOUT = "fix10_soldout_folder"     # 전상품품절을 옮길 임의분류


def move_to_folder(session, product_ids: list, folder: str,
                   ct: str = "B", timeout: int = 120) -> dict:
    """
    고른 광고상품의 **임의분류를 바꾼다**. 화면의 [분류변경] 과 같다.

        checkedit('classfy_edit', form)
          -> POST /manager/commercial/commercial_tiedlist
             action_mode=classfy_edit, site_categoryname=<옮길 폴더>,
             product_id[]=...

    전상품품절 상품을 '90.품절상품' 같은 곳으로 넘길 때 쓴다. 옮길 폴더는
    사용자마다 다르므로 설정에서 읽는다(2026-09-06 사용자 설명).
    """
    ids = [str(x) for x in (product_ids or []) if str(x).strip()]
    if not ids or not folder:
        return {"moved": 0, "message": "대상 없음"}
    data = {"action_mode": "classfy_edit", "commercial_type": ct,
            "site_categoryname": folder, "product_id[]": ids}
    r = session.post(URL_LIST, data=data, timeout=timeout)
    r.encoding = "utf-8"
    ok = r.status_code == 200
    return {"moved": len(ids) if ok else 0, "folder": folder,
            "message": "분류변경" if ok else f"HTTP {r.status_code}"}


def soldout_rows(session, folder: str, viewnum: int = 500,
                 timeout: int = 180) -> list:
    """
    그 폴더에서 **수정사항이 있으면서 품절**인 광고상품.

    사람이 하던 검색과 같다 — 임의분류 고르고, 정보수정 '수정사항유',
    좌측 상태를 '품절' 로 두고 검색한다.
    """
    data = _search_data(folder, True, viewnum)
    data["state"] = "soldout"
    r = session.post(URL_LIST, data=data, timeout=timeout)
    r.encoding = "utf-8"
    return parse_rows(r.text)


def move_rows(session, rows: list, target: str, from_folder: str = "",
              log=print) -> int:
    """
    품절 상품을 옮기고 **내역을 남긴다**. 언제·어디서·어디로·무엇을.

    나중에 "이 상품이 왜 품절폴더에 있지" 를 되짚을 수 있어야 한다
    (2026-09-06 사용자 요청). 로컬 SQLite 와 서버 MySQL 양쪽에 남는다.
    """
    if not rows or not target:
        return 0
    res = move_to_folder(session, [r["product_id"] for r in rows], target)
    ok = res["moved"] > 0
    db.save_soldout_moves(rows, from_folder, target, "ok" if ok else "fail")
    log(f"[품절] {from_folder} -> {target} {res['moved']}건 "
        f"({db.now_str()})")
    for r in rows[:20]:
        log(f"   {r['product_code']}  {(r.get('title') or '')[:34]}")
    return res["moved"]


def sweep_soldout(session, folder: str, target: str = "", log=print) -> dict:
    """
    품절 상품을 찾아 지정한 폴더로 넘긴다. 화면에서 전체선택 후 [분류변경]
    누르는 것과 같다. `target` 이 비면 옮기지 않고 목록만 돌려준다.
    """
    rows = soldout_rows(session, folder)
    log(f"[품절] {folder} — 수정사항 있는 품절 {len(rows)}건")
    for r in rows[:20]:
        log(f"   {r['product_code']}  {r['title'][:36]}")
    if not rows or not target:
        return {"found": len(rows), "moved": 0, "rows": rows}
    n = move_rows(session, rows, target, folder, log=log)
    return {"found": len(rows), "moved": n, "rows": rows}


# ---------------------------------------------------------------- 폼 재현

def serialize_form(html_text: str, name: str = "tieForm") -> tuple:
    """
    폼을 브라우저가 보내는 대로 만든다. (필드, action) 을 돌려준다.

    - 체크박스·라디오는 checked 인 것만 담는다
    - 파일 입력은 비워 보낸다 (이미지를 새로 올리는 게 아니다)
    - 같은 이름이 여러 개면 목록으로 담는다 (`opt_text[]` 같은 것)
    """
    i = html_text.find(f'name="{name}"')
    if i < 0:
        raise RuntimeError(f"{name} 을 찾지 못했습니다")
    start = html_text.rfind("<form", 0, i)
    tag = html_text[start:html_text.find(">", i) + 1]
    action = ""
    m = re.search(r'action="([^"]*)"', tag)
    if m:
        action = _html.unescape(m.group(1))
    body = html_text[start:html_text.find("</form>", start)]

    fields = {}

    def put(k, v):
        if k in fields:
            if not isinstance(fields[k], list):
                fields[k] = [fields[k]]
            fields[k].append(v)
        else:
            fields[k] = v

    for tg in re.findall(r"<input[^>]*>", body):
        n = re.search(r'name="([^"]*)"', tg)
        if not n:
            continue
        ty = (re.search(r'type="([^"]*)"', tg) or None)
        ty = ty.group(1).lower() if ty else "text"
        if ty in ("submit", "button", "reset", "image", "file"):
            continue
        if ty in ("checkbox", "radio") and "checked" not in tg:
            continue
        v = re.search(r'value="([^"]*)"', tg)
        put(n.group(1), _html.unescape(v.group(1)) if v else "")

    for m in re.finditer(r"<select[^>]*name=\"([^\"]+)\"[^>]*>(.*?)</select>",
                         body, re.S):
        opts = re.findall(r"<option[^>]*value=\"([^\"]*)\"([^>]*)>", m.group(2))
        val = ""
        if opts:
            val = opts[0][0]
            for ov, rest in opts:
                if "selected" in rest:
                    val = ov
                    break
        put(m.group(1), _html.unescape(val))

    for m in re.finditer(r"<textarea[^>]*name=\"([^\"]+)\"[^>]*>(.*?)</textarea>",
                         body, re.S):
        put(m.group(1), _html.unescape(m.group(2)))

    return fields, action


def open_edit(session, pid: str, ct: str = "B", timeout: int = 180) -> dict:
    """
    1단계 화면을 연다. 묶인 상품과 저장에 필요한 값을 뽑아 온다.

    반환 {'children': [...], 'soldout': 전부품절, 'data': 2단계로 보낼 값}
    """
    r = session.get(URL_EDIT1.format(pid=pid, ct=ct), timeout=timeout)
    r.encoding = "utf-8"
    h = r.text
    m = _RE_TIED.search(h)
    if not m:
        raise RuntimeError("묶인 상품 목록(tiedProducts)을 찾지 못했습니다")
    children = json.loads(m.group(1))
    fields, _ = serialize_form(h)

    # checkTieForm() 이 계산해 넣는 값들을 그대로 만든다
    fields["child_ids"] = ",".join(str(p["product_id"]) for p in children)
    d = _RE_DEFID.search(h)
    if d:
        fields["child_main_id"] = d.group(1)
    t3 = _RE_IMGT3.search(h)
    picked = [x for x in (t3.group(1).split(",") if t3 else []) if x]
    codes = {str(p.get("product_code")) for p in children}
    fields["img_turn2"] = ",".join([x for x in picked if x in codes])
    fields["is_simple"] = "Y"            # next() 가 넣는 값

    dead = [p for p in children
            if str(p.get("state")) in SOLDOUT_STATES
            or str(p.get("isrejected")) == "ok"]
    return {"children": children, "data": fields,
            "soldout": bool(children) and len(dead) == len(children),
            "dead": len(dead)}


def fix_one(session, pid: str, ct: str = "B", timeout: int = 180,
            dry: bool = False, state: str = "") -> dict:
    """
    한 상품의 1.0 수정을 끝까지 진행한다.

    `state` 는 목록의 상태 칸 글이다. 거기에 '품절' 이 있으면 열어보지 않고
    건너뛴다 — 사용자가 말한 **유일한 오류 경우**가 전상품품절이다.
    목록이 품절이라고 하는데 묶인 상품은 판매중으로 오는 경우가 실제로
    있어서(LCP_LHA_B910630), 목록 표시도 같이 본다(2026-09-06 실측).

    반환 {'result': ok|soldout|fail, 'message': ...}
    """
    if "품절" in (state or ""):
        return {"result": "soldout", "message": "목록에 품절로 표시됨",
                "children": 0}
    ctx = open_edit(session, pid, ct, timeout)
    if ctx["soldout"]:
        return {"result": "soldout", "message": "묶인 상품이 전부 품절",
                "children": len(ctx["children"])}
    if dry:
        return {"result": "dry", "message": f"묶음 {len(ctx['children'])}건",
                "children": len(ctx["children"])}

    # 1단계 -> 2단계
    r = session.post(URL_STEP1.format(pid=pid, ct=ct), data=ctx["data"],
                     timeout=timeout)
    r.encoding = "utf-8"
    if "tieForm" not in r.text:
        raise RuntimeError(f"2단계가 열리지 않았습니다 (HTTP {r.status_code})")

    # 2단계부터는 화면이 스스로 다음 단계로 넘어간다.
    #   Edit2 --[저장 후 닫기]--> Edit3 --(load 시 자동 submit)--> Edit4 (저장)
    # Edit4 는 자기 자신을 다시 가리키므로 **같은 단계로 두 번 보내지 않는다**.
    # 두 번째부터는 이미 저장한 것을 또 저장하는 헛일이다.
    # 마지막 Edit4 는 자기 자신으로 되돌아오면서 마켓을 하나씩 처리한다.
    # 그래서 **화면이 더 변하지 않을 때까지** 보낸다 - 브라우저가 load 마다
    # 자동 submit 하는 것과 같은 동작이다. 한 번만 보내면 목록에서 안 빠진다
    # (2026-09-05 실측: 한 번=그대로, 끝까지=목록에서 빠짐).
    steps = []
    page = r.text
    prev = ""
    for _ in range(8):
        fields, action = serialize_form(page)
        if not action:
            break
        steps.append(action.split("/popup")[0].rsplit("/", 1)[-1])
        url = action if action.startswith("http") else SITE + action
        rr = session.post(url, data=fields, timeout=timeout)
        rr.encoding = "utf-8"
        if rr.text == prev:
            break                       # 더 변하지 않는다 - 끝났다
        prev, page = rr.text, rr.text
    ok = bool(steps) and steps[-1].endswith("Edit4")
    return {"result": "ok" if ok else "fail",
            "message": " > ".join(x.replace("commercial_prodtie2", "")
                                  for x in steps),
            "children": len(ctx["children"])}


def _follow(session, page: str, timeout: int = 180) -> list:
    """마법사가 더 변하지 않을 때까지 폼을 이어 보낸다. 지나온 단계를 준다."""
    steps, prev = [], ""
    for _ in range(8):
        fields, action = serialize_form(page)
        if not action:
            break
        steps.append(action.split("/popup")[0].rsplit("/", 1)[-1])
        url = action if action.startswith("http") else SITE + action
        rr = session.post(url, data=fields, timeout=timeout)
        rr.encoding = "utf-8"
        if rr.text == prev:
            break
        prev, page = rr.text, rr.text
    return steps


def set_keywords(session, pid: str, words, ct: str = "B", timeout: int = 180,
                 merge: bool = True) -> dict:
    """
    LCP 의 **키워드 관리(희망검색어)** 를 바꾼다.

    상품명 낱말을 키워드로 넣어두면 로하스가 만들어 주는 태그·상품명 후보가
    그만큼 넓어진다. 후보가 1~2개뿐이라 태그를 못 채우던 LCP 에 쓴다
    (2026-09-06 사용자 요청).

    화면과 같은 경로다 — 1.0 수정 마법사 2단계의 `keywords` 칸을 고쳐
    끝까지 저장한다. `merge` 면 기존 키워드에 더한다.
    """
    ctx = open_edit(session, pid, ct, timeout)
    r = session.post(URL_STEP1.format(pid=pid, ct=ct), data=ctx["data"],
                     timeout=timeout)
    r.encoding = "utf-8"
    if "tieForm" not in r.text:
        raise RuntimeError(f"2단계가 열리지 않았습니다 (HTTP {r.status_code})")

    fields, action = serialize_form(r.text)
    cur = str(fields.get("keywords") or "")
    old = [w for w in re.split(r"[\s,]+", cur) if w]
    new = [w for w in words if w]
    keep = old + [w for w in new if w not in old] if merge else new
    # 띄어쓰기 단위를 줄바꿈으로 나눠 넣는다 (사용자 지시)
    fields["keywords"] = chr(10).join(keep)

    url = action if action.startswith("http") else SITE + action
    rr = session.post(url, data=fields, timeout=timeout)
    rr.encoding = "utf-8"
    steps = ["Edit3"] + _follow(session, rr.text, timeout)
    return {"before": len(old), "after": len(keep), "added": len(keep) - len(old),
            "keywords": keep, "steps": steps}


def read_keywords(session, pid: str, ct: str = "B", timeout: int = 180) -> list:
    """지금 들어 있는 키워드를 읽는다 (저장하지 않는다)."""
    ctx = open_edit(session, pid, ct, timeout)
    r = session.post(URL_STEP1.format(pid=pid, ct=ct), data=ctx["data"],
                     timeout=timeout)
    r.encoding = "utf-8"
    fields, _ = serialize_form(r.text)
    return [w for w in re.split(r"[\s,]+", str(fields.get("keywords") or "")) if w]


# ---------------------------------------------------------------- 일괄

def clone_session(base):
    """
    쿠키·헤더만 같은 새 세션. requests 세션은 스레드끼리 나눠 쓰지 않는다.

    로그인은 이미 되어 있으므로 쿠키만 복사하면 그대로 쓸 수 있다.
    """
    import requests
    s = requests.Session()
    s.cookies.update(base.cookies.get_dict())
    s.headers.update(dict(base.headers))
    return s


def bulk(session_factory, folder: str, *, both: bool = True, limit: int = 0,
         dry: bool = False, log=print, stop=None) -> dict:
    """
    폴더 하나를 통째로 처리한다. 기본은 **정방향·역방향 동시**다.

    session_factory() 는 스레드마다 하나씩 세션을 만들어 준다 — requests 세션은
    스레드 사이에 나눠 쓰지 않는 편이 안전하다.

    stop() 이 True 를 돌려주면 그 자리에서 멈춘다.
    """
    run_id = uuid.uuid4().hex[:12]
    ses0 = session_factory()
    rows = search(ses0, folder)
    if limit:
        rows = rows[:limit]
    total = len(rows)
    log(f"[수정1.0] {folder} — 수정사항 {total:,}건 "
        f"({'정·역 동시' if both else '정방향'})")
    if not total:
        return {"run_id": run_id, "total": 0, "ok": 0, "soldout": 0, "fail": 0}

    lock = threading.Lock()
    done = set()
    cnt = {"ok": 0, "soldout": 0, "fail": 0, "n": 0}
    t0 = time.time()

    def take(order):
        """앞(정방향) 또는 뒤(역방향)에서 아직 아무도 안 잡은 것을 하나 집는다."""
        with lock:
            seq = rows if order == "asc" else reversed(rows)
            for r in seq:
                if r["product_id"] not in done:
                    done.add(r["product_id"])
                    return r
        return None

    def worker(order):
        s = session_factory()
        while True:
            if stop and stop():
                return
            row = take(order)
            if not row:
                return
            t1 = time.time()
            try:
                res = fix_one(s, row["product_id"], dry=dry,
                              state=row.get("state", ""))
            except Exception as e:
                res = {"result": "fail", "message": str(e)[:120]}
            el = time.time() - t1
            with lock:
                cnt["n"] += 1
                cnt[res["result"] if res["result"] in cnt else "ok"] += 1
                i = cnt["n"]
            db.save_fix10_log(run_id, folder, row, order, res, el)
            mark = {"ok": "완료", "soldout": "품절-건너뜀",
                    "dry": "미리보기", "fail": "실패"}.get(res["result"], "?")
            log(f"  [{i}/{total}] {'▶' if order == 'asc' else '◀'} "
                f"{row['product_code']} {mark} {res.get('message', '')[:40]}"
                f" ({el:.1f}초)")

    orders = ["asc", "desc"] if both else ["asc"]
    ths = [threading.Thread(target=worker, args=(o,), daemon=True)
           for o in orders]
    for t in ths:
        t.start()
    for t in ths:
        t.join()

    el = time.time() - t0
    log(f"[수정1.0] 끝 — 완료 {cnt['ok']:,} / 품절 {cnt['soldout']:,} / "
        f"실패 {cnt['fail']:,} ({el / 60:.1f}분)")
    return {"run_id": run_id, "total": total, "seconds": el, **cnt}
