"""
LCP 「키워드관리」 화면 (attr 팝업의 [포함상품보기] 아래 버튼).

    editkeyword3(product_id, cate, product_code)
      -> /manager/commercial/commercial_keyword_auto2/popup/ok
           /product_id/{id}/product_code/{LCP}/fc/A001/fv//orderby/keyword

화면 구조
    왼쪽  후보 키워드 체크박스 (name="keywords")  = 사용 키워드로 등록할 것
    오른쪽 textarea name="fk"                     = **한 줄에 하나씩** 적는 칸
    [저장] selectkeyword('add', form)

저장은 `chkForm` 을 그대로 보낸다.
    POST .../commercial_keyword_auto2/pop/ok/product_id/{id}/product_code/{LCP}/fc/A001
      action_mode   = add
      keywords_new  = 체크된 후보들 (콤마 구분)
      keywords_new2 = fk 내용을 'a'|'b'|'c' 로 이어붙인 것
fk 는 저장 전에 **띄어쓰기와 괄호를 지운다** — 화면 JS 가 그렇게 한다
    key.replace(/ /g, "")          공백 제거
    key.replace(/ *\\([^)]*\\) */g, "")  '( 1,234 )' 같은 조회수 제거
"""
import html as _html
import re

from . import constants as C

BASE = C.BASE + "/manager/commercial/commercial_keyword_auto2"
URL_VIEW = (BASE + "/popup/ok/product_id/{pid}/product_code/{code}"
            "/fc/A001/fv//orderby/keyword")
URL_SAVE = BASE + "/pop/ok/product_id/{pid}/product_code/{code}/fc/A001"

_PAREN = re.compile(r"\s*\([^)]*\)\s*")
MAX_KEYWORDS = 500          # 화면이 막는 상한


def _clean(line: str) -> str:
    """화면 JS 와 같은 정리 — 조회수 괄호와 공백을 뗀다."""
    return _PAREN.sub("", line or "").replace(" ", "").strip()


def fetch(session, product_id, product_code: str, timeout: int = 90) -> dict:
    """
    키워드관리 화면을 읽는다.

    반환 {'fk': [줄], 'checked': [사용 키워드], 'fields': {폼 값}, 'html': ...}
    """
    r = session.get(URL_VIEW.format(pid=product_id, code=product_code),
                    timeout=timeout)
    r.encoding = "utf-8"
    h = r.text
    if "chkForm" not in h:
        raise RuntimeError("키워드관리 화면을 열지 못했습니다")

    m = re.search(r'<textarea[^>]*name="fk"[^>]*>(.*?)</textarea>', h, re.S)
    raw = _html.unescape(m.group(1)) if m else ""
    fk = [x for x in (_clean(v) for v in raw.split("\n")) if x]

    checked = []
    for tag in re.findall(r"<input[^>]*>", h):
        if 'name="keywords"' not in tag or "checked" not in tag:
            continue
        v = re.search(r'value="([^"]*)"', tag)
        if v:
            checked.append(_html.unescape(v.group(1)))

    # 폼의 나머지 값 (숨은 값들)
    i = h.find('name="chkForm"')
    start = h.rfind("<form", 0, i) if i > 0 else 0
    body = h[start:h.find("</form>", start)] if i > 0 else h
    fields = {}
    for tag in re.findall(r"<input[^>]*>", body):
        n = re.search(r'name="([^"]*)"', tag)
        if not n or n.group(1) in ("keywords", "fk"):
            continue
        ty = re.search(r'type="([^"]*)"', tag)
        ty = ty.group(1).lower() if ty else "text"
        if ty in ("submit", "button", "reset", "image", "file"):
            continue
        if ty in ("checkbox", "radio") and "checked" not in tag:
            continue
        v = re.search(r'value="([^"]*)"', tag)
        fields[n.group(1)] = _html.unescape(v.group(1)) if v else ""
    return {"fk": fk, "checked": checked, "fields": fields, "html": h}


def save(session, product_id, product_code: str, fk_lines: list,
         checked: list = None, base_fields: dict = None,
         timeout: int = 120) -> dict:
    """
    키워드관리 저장. 화면의 [저장] 과 같은 요청이다.

    `fk_lines` 가 그 상품의 키워드 목록이 된다(한 줄에 하나). 500개가 넘으면
    화면과 같이 앞 500개만 보낸다.
    """
    lines = [x for x in (_clean(v) for v in (fk_lines or [])) if x]
    seen, uniq = set(), []
    for w in lines:
        if w in seen:
            continue
        seen.add(w)
        uniq.append(w)
    cut = uniq[:MAX_KEYWORDS]

    data = dict(base_fields or {})
    data["action_mode"] = "add"
    data["fk"] = "\n".join(cut)
    data["keywords_new"] = ",".join(checked or [])
    data["keywords_new2"] = "|".join(f"'{w}'" for w in cut)
    if checked:
        data["keywords"] = list(checked)

    r = session.post(URL_SAVE.format(pid=product_id, code=product_code),
                     data=data, timeout=timeout)
    r.encoding = "utf-8"
    return {"ok": r.status_code == 200, "sent": len(cut),
            "over": max(0, len(uniq) - MAX_KEYWORDS)}
