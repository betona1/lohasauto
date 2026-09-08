"""
카테고리 저장 (쓰기).

읽기는 전부 HTTP 로 됐지만 저장 엔드포인트를 못 찾아 막혀 있던 부분이다.
attr 팝업 HTML 안의 saveCategory() 를 그대로 재현한다.

  후보 목록  GET  /manager/commercial/ajax_ss_attr/rmt/ok/mode/prod_category/code/{lcp_code}
             -> [{code, name, cnt, unit, capacity}, ...]  cnt 내림차순
  저장       POST /manager/commercial/ajax_ss_attr/rmt/ok/mode/save_cate
             no, product_id, leaf, capacity, unit, total_capacity
             -> {"message": "...", "attr": [...]}
  속성 조회  POST /manager/commercial/ajax_ss_attr/rmt/ok/mode/attr   (leaf)

product_id 는 L코드에서 'L' 과 선행 0 을 뗀 값이다.
  L0786352 -> 786352 / L5055606 -> 5055606 / L3939928 -> 3939928

주의 — 사이트가 경고하는 그대로다.
  "카테고리 변경 저장시 속성 및 상품명/태그의 값이 초기화 됩니다."
이미 카테고리가 저장된 상품에 다른 코드를 넣으면 기존 작업이 날아간다.
그래서 save_category() 는 기본적으로 덮어쓰기를 거부한다(allow_change=True 필요).
"""
import json
import re
import time

from . import constants as C

AJAX = C.BASE + "/manager/commercial/ajax_ss_attr/rmt/ok/mode"
URL_CANDIDATES = AJAX + "/prod_category/code/{lcp_code}"
URL_SAVE_CATE = AJAX + "/save_cate"
URL_ATTR = AJAX + "/attr"

_RE_LCODE = re.compile(r"^L?0*(\d+)$", re.I)


def product_id_of(l_code: str) -> str:
    """L코드 -> product_id (L 과 선행 0 제거)."""
    m = _RE_LCODE.match((l_code or "").strip())
    if not m:
        raise ValueError(f"L코드 형식이 아닙니다: {l_code!r}")
    return m.group(1)


def _json(resp):
    resp.encoding = "utf-8"
    if "loginForm" in resp.text:
        raise RuntimeError("세션 만료")
    try:
        return json.loads(resp.text)
    except Exception:
        raise RuntimeError(f"JSON 아님 (HTTP {resp.status_code}): "
                           f"{resp.text[:160]}")


def fetch_candidates(session, lcp_code: str, timeout=30) -> list:
    """LCP 의 카테고리 후보. 화면 라디오 목록과 같은 순서(첫 항목이 기본 선택)."""
    r = session.get(URL_CANDIDATES.format(lcp_code=lcp_code), timeout=timeout)
    d = _json(r)
    return d if isinstance(d, list) else []


def fetch_attributes(session, leaf: str, timeout=30) -> list:
    """카테고리에 딸린 속성 정의. 저장 없이 조회만 한다."""
    r = session.post(URL_ATTR, data={"leaf": str(leaf)}, timeout=timeout)
    d = _json(r)
    return d if isinstance(d, list) else []


_CAP_G = {"KG": 1000, "K": 1000, "G": 1, "GR": 1, "㎏": 1000, "T": None}
_CAP_ML = {"L": 1000, "ML": 1, "리터": 1000, "CC": 1}
_CAP_EA = {"개": 1, "매": 1, "장": 1, "정": 1, "포": 1, "T": 1, "티백": 1}
# 수량 단위. PET·펫(음료 병)과 묶음(박스·줄·판)도 수량이다 —
# '1.25L 12PET' 을 못 읽어 12를 놓쳤다(2026-09-07 코카콜라).
_QTY = (r"(?:개입|개|입|팩|봉|포|매|장|병|캔|박스|BOX|줄|판|세트|SET"
        r"|PET|펫|EA|P)")


def parse_capacity(name: str, unit: str = "g"):
    """
    원상품명에서 **총 용량**을 읽는다. 단위용량 × 수량이다.

        '오쉐프 오곡잣호두율무차 800G 12개'  ->  800 × 12 = 9,600 g
        '꽃샘 꿀생강차(S) 1kg X2'            -> 1000 × 2  = 2,000 g
        '희창 대추생강차 900g x 12봉입'      ->  900 × 12 = 10,800 g

    **모르겠으면 None 을 준다.** 찍어서 넣지 않는다 — 용량이 틀리면
    반품 사유다(사용자 지침 2026-09-06).
      · 단위가 안 적힌 것        '담터 생강차 플러스 50T'
      · 1회분이 안 적힌 것       '꿀생강차 포션(15입)'
      · 용량 표기가 둘 이상인 것
    """
    u = (unit or "g").strip().lower()
    tab = _CAP_G if u in ("g", "kg", "㎏") else (
        _CAP_ML if u in ("ml", "l", "cc") else _CAP_EA)
    keys = sorted([k for k, v in tab.items() if v], key=len, reverse=True)
    if not keys:
        return None, ""
    t = (name or "").upper().replace(",", "")
    # '450gX4ea' 처럼 단위 뒤에 바로 곱하기가 붙는 표기도 읽어야 한다.
    # 단위 다음이 글자면 무조건 막았더니 못 읽었다(2026-09-06).
    pat = (r"([0-9]+(?:\.[0-9]+)?)\s*(" + "|".join(map(re.escape, keys))
           + r")(?=$|[^A-Z0-9]|X[0-9])")
    m = list(re.finditer(pat, t))
    if not m:
        return None, ""
    # 상품명이 **총량을 직접 적어둔** 경우가 있다 — '800g X 5봉지 (총 4kg)'.
    # 그럴 땐 계산할 것 없이 그 값을 쓴다. 표기가 둘이라고 포기하면 안 된다
    # (2026-09-07 LCP_LHA_B915223).
    tot = re.search(r"총\s*([0-9]+(?:\.[0-9]+)?)\s*(" +
                    "|".join(map(re.escape, keys)) + r")(?![0-9A-Z])", t)
    if tot:
        v = float(tot.group(1)) * tab[tot.group(2)]
        if v > 0:
            return int(round(v)), "총 " + tot.group(0).replace("총", "").strip()
    if len(m) > 1:
        return None, "용량 표기가 여러 개"
    val = float(m[0].group(1)) * tab[m[0].group(2)]
    rest = t[m[0].end():]
    # 수량은 **여러 겹**일 수 있다. '300ml x 24펫 x 2박스' 는 24 x 2 다.
    # 앞의 하나만 읽어 24를 놓치고 2만 쓴 적이 있다(2026-09-07 코카콜라).
    # 뒤에 또 곱하기가 붙는 표기('24펫X2박스')도 읽어야 한다. 그냥
    # (?![0-9A-Z]) 로 막으면 뒤의 X 때문에 24펫이 통째로 안 잡힌다.
    qs = [int(x) for x in
          re.findall(r"([0-9]+)\s*" + _QTY + r"(?=$|[^0-9A-Z]|X[0-9])", rest)]
    if not qs:                      # '1kg X2' 처럼 곱하기만 적힌 경우
        qs = [int(x) for x in
              re.findall(r"[X*]\s*([0-9]+)(?![0-9A-Z])", rest)]
    n = 1
    for x in qs:
        n *= x
    if n > 1000 or val <= 0:
        return None, "수량이 이상함"
    how = m[0].group(0) + ("".join(f" x {x}" for x in qs) if qs else "")
    return int(round(val * n)), how


# 로하스 목록 화면의 검색 필터. 「대표이미지=이미지승인완료」 +
# 「카테고리=작업완료-총용량확인」 으로 검색하면 **총 용량을 넣어야 할 상품**이
# 그대로 나온다. 우리가 DB 로 추정하던 것보다 정확하다
# (2026-09-07 사용자가 알려줌 — 594 폴더 기준 추정 241건 vs 실제 514건).
CAPACITY_FILTER = "capacity"


def capacity_todo(client, folder: str, dest_list: str = "allow",
                  timeout: int = 240) -> list:
    """
    총 용량 확인이 필요한 L코드 목록. 사이트 검색 그대로다.

    반환 [{'lcp_code','l_code','product_no'}, ...]
    """
    data = client._payload(folder, dest_list=dest_list, viewnum="100000")
    data["dest_cate"] = CAPACITY_FILTER
    r = client.session.post(
        C.BASE + "/manager/commercial/commercial_ss_image",
        data=data, timeout=timeout)
    r.encoding = "utf-8"
    out = []
    for row in client.parse_rows(r.text):
        lcp, lcode, no = (list(row) + ["", "", ""])[:3]
        out.append({"lcp_code": lcp, "l_code": lcode, "product_no": no})
    # 목록에는 product_no 가 없다. 우리가 수집해둔 대응표에서 채운다.
    need = [x["l_code"] for x in out if not x["product_no"]]
    if need:
        from .. import db as _db

        with _db.sqlite_conn() as c:
            m = {r2["l_code"]: r2["product_no"] for r2 in c.execute(
                "SELECT l_code, product_no FROM lcode_attr "
                "WHERE l_code IN (%s)" % ",".join("?" * len(need)), need)}
        for x in out:
            if not x["product_no"]:
                x["product_no"] = m.get(x["l_code"], "")
    return out


def saved_capacity(session, product_no, timeout=60) -> str:
    """지금 저장돼 있는 총 용량. 없으면 ''."""
    from . import tabs
    r = session.get(tabs.URL_ATTR.format(no=product_no), timeout=timeout)
    r.encoding = "utf-8"
    m = re.search(r"var\s+total_capacity_value\s*=\s*([^;]*);", r.text)
    v = m.group(1).strip() if m else ""
    return v.strip(chr(34) + chr(39))


def save_category(session, product_no, l_code, leaf, *, capacity="", unit="",
                  total_capacity="", current="", allow_change=False,
                  timeout=40) -> dict:
    """
    카테고리 한 건 저장.

    current      현재 저장된 etc_category (알고 있으면 넘긴다)
    allow_change current 와 다른 코드로 바꿀 때만 True. 기존 속성·상품명·태그가
                 초기화되므로 기본값은 거부다.
    """
    leaf = str(leaf).strip()
    if not leaf:
        raise ValueError("leaf(카테고리 코드)가 비었습니다.")
    cur = str(current or "").strip()
    if cur and cur != leaf and not allow_change:
        raise RuntimeError(
            f"{l_code}: 이미 카테고리 {cur} 가 저장돼 있습니다. "
            f"{leaf} 로 바꾸면 속성·상품명·태그가 초기화됩니다 "
            f"(allow_change=True 로만 진행).")

    data = {
        "no": str(product_no),
        "product_id": product_id_of(l_code),
        "leaf": leaf,
        "capacity": str(capacity or ""),
        "unit": str(unit or ""),
        "total_capacity": str(total_capacity or ""),
    }
    r = session.post(URL_SAVE_CATE, data=data, timeout=timeout)
    res = _json(r)
    msg = (res or {}).get("message", "")
    return {
        "ok": bool(msg) and "저장" in msg,
        "message": msg,
        "attr_count": len((res or {}).get("attr") or []),
        "leaf": leaf,
        "product_no": str(product_no),
        "l_code": l_code,
        "sent": data,
    }


def pick(candidates: list, *, prefer: str = "") -> dict:
    """
    후보 중 하나를 고른다. 규칙은 화면 기본값과 같다 — 건수(cnt) 1위.
    prefer 가 주어지면 그 코드를 우선한다.
    unit 이 붙은 후보(용량·수량 입력이 필요한 식품류 등)는 자동 대상이 아니므로
    needs_manual=True 로 표시해 돌려준다. 호출 쪽에서 걸러야 한다.
    """
    if not candidates:
        return {}
    c = None
    if prefer:
        c = next((x for x in candidates if str(x.get("code")) == str(prefer)),
                 None)
    if c is None:
        c = candidates[0]
    top = candidates[0]
    return {
        "code": str(c.get("code") or ""),
        "name": c.get("name") or "",
        "cnt": int(str(c.get("cnt") or 0) or 0),
        "unit": c.get("unit") or "",
        "capacity": c.get("capacity") or "",
        "needs_manual": bool(c.get("unit")),
        "rival_cnt": int(str(top.get("cnt") or 0) or 0) if c is not top else 0,
        "n_candidates": len(candidates),
    }
