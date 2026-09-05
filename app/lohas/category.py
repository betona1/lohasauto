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
