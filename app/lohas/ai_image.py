"""
AI 이미지 일괄수정 (상품정보관리 화면의 [선택상품 AI이미지 일괄수정]).

화면 동작을 그대로 재현한 것이다 — 브라우저는 필요 없다.

    폴더 선택 → 페이지당 1000개 → n페이지로 이동
    → 페이지 전체 체크 → [선택상품 AI이미지 일괄수정]
    → 작업명 입력 / 질의어는 기본값 '잘어울리는 배경' 그대로 → [적용하기]

사이트 코드(`makeAiImg_list` / `submitAiImg`)가 하는 일은 이것뿐이다.

    form.action_mode = 'make_ai_list'
    form.ai_title    = 작업명
    form.ai_query    = 질의어
    POST /manager/commercial/commercial_ss_image/p/{page}

`no[]` 는 그 페이지의 체크박스다. 전상품선택(`all_product`)은 1000개를
넘으면 사이트가 막으므로 쓰지 않는다 — **페이지 단위로 1000개씩** 건다.

작업 결과는 별도 화면에서 본다.
    /manager/commercial/commercial_ss_image_job_list/popup/ok
"""
import html as _html
import re

from . import constants as C

LIST_URL = C.BASE + "/manager/commercial/commercial_ss_image"
JOB_URL = (C.BASE
           + "/manager/commercial/commercial_ss_image_job_list/popup/ok")
DEFAULT_QUERY = "잘어울리는 배경"

_TAG = re.compile(r"<(input|select|textarea)\b[^>]*>", re.I)
_ATTR = re.compile(r'(\w[\w-]*)\s*=\s*"([^"]*)"')


def _attrs(tag: str) -> dict:
    return {k.lower(): _html.unescape(v) for k, v in _ATTR.findall(tag)}


def form_values(html: str) -> dict:
    """
    `listForm` 이 지금 담고 있는 값. 브라우저가 submit 할 때 보내는 것과 같다.

    - select 는 `selected` 인 option (없으면 첫 option)
    - checkbox/radio 는 `checked` 인 것만
    - `no[]` 와 `all_product` 는 제외한다 (고르는 쪽에서 따로 넣는다)
    """
    i = html.find('id="listForm"')
    if i < 0:
        raise RuntimeError("listForm 을 찾지 못했습니다")
    st = html.rfind("<form", 0, i)
    body = html[st:html.find("</form>", st)]

    out = {}
    for m in _TAG.finditer(body):
        kind, tag = m.group(1).lower(), m.group(0)
        a = _attrs(tag)
        name = a.get("name")
        if not name or name in ("no[]", "all_product"):
            continue
        if kind == "input":
            ty = (a.get("type") or "text").lower()
            if ty in ("submit", "button", "reset", "image", "file"):
                continue
            if ty in ("checkbox", "radio") and "checked" not in tag.lower():
                continue
            out[name] = a.get("value", "")
        elif kind == "textarea":
            end = body.find("</textarea>", m.end())
            out[name] = _html.unescape(body[m.end():end]) if end > 0 else ""
        else:                                   # select
            end = body.find("</select>", m.end())
            opts = re.findall(r"<option\b[^>]*>", body[m.end():end])
            pick = next((o for o in opts if "selected" in o.lower()), None)
            pick = pick or (opts[0] if opts else "")
            out[name] = _attrs(pick).get("value", "") if pick else ""
    return out


def page_nos(html: str) -> list:
    """그 페이지의 상품 체크박스 값(=no). 화면의 '페이지선택' 과 같다."""
    out = []
    for tag in re.findall(r"<input\b[^>]*>", html):
        a = _attrs(tag)
        if a.get("name") == "no[]" and a.get("value"):
            out.append(a["value"])
    return out


def fetch_page(client, folder: str, page: int = 1, viewnum: str = "1000",
               timeout: int = 240) -> dict:
    """폴더 + 페이지당 개수 + 페이지 번호로 목록을 연다."""
    data = client._payload(folder, viewnum=viewnum)
    url = LIST_URL + (f"/p/{page}" if page and page > 1 else "")
    r = client.session.post(url, data=data, timeout=timeout)
    r.encoding = "utf-8"
    h = r.text
    if 'id="loginForm"' in h:
        raise RuntimeError("세션이 만료되었습니다")
    return {"html": h, "url": url, "nos": page_nos(h),
            "form": form_values(h)}


def run_page(client, folder: str, page: int, title: str,
             query: str = DEFAULT_QUERY, viewnum: str = "1000",
             dry: bool = True, timeout: int = 300) -> dict:
    """
    한 페이지(최대 1000건)를 AI 이미지 일괄수정에 건다.

    `dry=True` 면 보낼 내용만 계산하고 **아무것도 하지 않는다**.
    """
    pg = fetch_page(client, folder, page, viewnum)
    nos = pg["nos"]
    if not nos:
        return {"ok": False, "count": 0, "reason": "그 페이지에 상품이 없습니다"}
    if len(nos) > 1000:
        return {"ok": False, "count": len(nos),
                "reason": "1000개를 넘습니다 - 페이지당 개수를 줄이십시오"}

    data = dict(pg["form"])
    data["action_mode"] = "make_ai_list"
    data["ai_title"] = title
    data["ai_query"] = query
    data["no[]"] = nos
    if dry:
        return {"ok": True, "dry": True, "count": len(nos), "url": pg["url"],
                "title": title, "query": query,
                "first": nos[0], "last": nos[-1]}

    r = client.session.post(pg["url"], data=data, timeout=timeout)
    r.encoding = "utf-8"
    body = r.text
    msg = ""
    m = re.search(r"alert\(['\"](.*?)['\"]\)", body)
    if m:
        msg = m.group(1)
    return {"ok": r.status_code == 200, "count": len(nos), "url": pg["url"],
            "title": title, "query": query, "status": r.status_code,
            "message": msg, "first": nos[0], "last": nos[-1]}


# ---------------------------------------------------------------- 작업 결과
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_STRIP = re.compile(r"<[^>]+>")


def _text(s: str) -> str:
    return _html.unescape(_STRIP.sub(" ", s)).replace("&nbsp;", " ").strip()


def jobs(session, timeout: int = 120) -> list:
    """
    AI 이미지 일괄수정 **작업결과** 목록.

    화면 그대로 읽는다. 컬럼 이름은 표의 머리글을 쓴다 — 사이트가 바뀌어도
    이름으로 찾을 수 있게 하려는 것이다.
    """
    r = session.get(JOB_URL, timeout=timeout)
    r.encoding = "utf-8"
    h = r.text
    rows = [[_text(c) for c in _CELL.findall(x)] for x in _ROW.findall(h)]
    rows = [x for x in rows if x]
    if not rows:
        return []
    head = rows[0]
    out = []
    for x in rows[1:]:
        if len(x) < 2:
            continue
        out.append({head[i] if i < len(head) else f"col{i}": v
                    for i, v in enumerate(x)})
    return out


def total_pages(folder: str, per: int = 1000) -> int:
    """
    그 폴더가 몇 페이지인지. **점검 기록(DB)에서 센다** — 페이지 수를 알자고
    사이트를 다시 긁지 않는다(2026-09-07 사용자: 불필요한 크롤링 자제).
    기록이 없으면 0.
    """
    import math

    from .. import db

    s = db.latest_scan(folder) or {}
    n = int(s.get("total_rows") or 0)
    return math.ceil(n / per) if n else 0


def title_for(base: str, page: int) -> str:
    """작업명 규칙 — 뒤 숫자를 페이지 번호로 바꾼다 (잘어울리는배경3)."""
    import re

    stem = re.sub(r"\d+$", "", (base or "").strip()) or "AI작업"
    return f"{stem}{page}"


def find_job(session, title: str, timeout: int = 120) -> dict:
    """작업명으로 방금 만든 작업을 찾는다."""
    for r in jobs(session, timeout=timeout):
        if (r.get("작업명/분류") or "").strip() == title.strip():
            return r
    return {}
