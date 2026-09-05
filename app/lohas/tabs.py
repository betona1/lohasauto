"""
상품정보 팝업(attr) / 태그·상품명 탭을 HTTP 로 읽는다.

04_로하스는 이 화면들을 브라우저(Playwright)로 열어 읽었지만, 실측해보니
전부 서버가 그려서 내려주는 정적 HTML이라 쿠키만 있으면 요청 한 번으로 읽힌다.
읽기는 브라우저가 전혀 필요 없다. (저장은 별도)

  attr      : /commercial_ss_image_attr/popup/ok/no/{no}
              원상품명, 이미지, 분석완료(analysis_date), 카테고리 상태
  셸        : /commercial_ss_title_tag/popup/ok/no/{no}
              savedTabsStatus (탭별 저장 여부)
  태그 탭   : /commercial_ss_tab_tag/popup/ok/no/{no}      -> tbody-tag
  상품명1   : /commercial_ss_tab_title/popup/ok/no/{no}    -> tbody-title
  상품명N   : /commercial_ss_tab_titleN/popup/ok/title/{n}/no/{no}
"""
import html as _html
import json
import re

from . import constants as C

MANAGER = C.BASE + "/manager/commercial"

URL_ATTR = MANAGER + "/commercial_ss_image_attr/popup/ok/no/{no}"
URL_SHELL = MANAGER + "/commercial_ss_title_tag/popup/ok/no/{no}"
URL_TAG = MANAGER + "/commercial_ss_tab_tag/popup/ok/no/{no}"
URL_TITLE1 = MANAGER + "/commercial_ss_tab_title/popup/ok/no/{no}"
URL_TITLEN = MANAGER + "/commercial_ss_tab_titleN/popup/ok/title/{n}/no/{no}"

_RE_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_RE_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_RE_CB = re.compile(r'<input[^>]*type=["\']?checkbox["\']?[^>]*>', re.S)
_RE_VAL = re.compile(r'value=["\']([^"\']*)["\']')
_RE_BANCATE = re.compile(r'data-ban-cate=["\']([^"\']*)["\']')
_RE_SAVED = re.compile(r"savedTabsStatus\s*=\s*(\{[^}]*\})")
_RE_ADATE = re.compile(r'let\s+analysis_date\s*=\s*"([^"]*)"')
# etc_category 는 따옴표 없는 숫자(50004771) 또는 빈 문자열("") 로 온다.
# 사이트도 이 값으로 카테고리 저장여부를 판단한다:
#   if (etc_category) { savedStatus[0] = true; }
_RE_ETCCAT = re.compile(
    r'(?:var|let|const)\s+etc_category\s*=\s*'
    r'(?:"([^"]*)"|\'([^\']*)\'|(\d+))')
_RE_NUM = re.compile(r"\d[\d,]*")


def _text(html: str) -> str:
    """태그 제거 + HTML 엔티티 해제(&nbsp; 등) + 공백 정리."""
    t = re.sub(r"<[^>]+>", " ", html or "")
    t = _html.unescape(t).replace(" ", " ")
    return " ".join(t.split())


def _tbody(html: str, tid: str) -> str:
    """<tbody id="..."> ... </tbody> 구간만 잘라낸다."""
    m = re.search(rf'<tbody[^>]*id=["\']{tid}["\'][^>]*>', html)
    if not m:
        return ""
    end = html.find("</tbody>", m.end())
    return html[m.end():end if end > 0 else len(html)]


def parse_keyword_rows(html: str, tid: str, is_tag: bool) -> list:
    """
    키워드 표를 행 목록으로. 04 의 TAG_PICK_JS 와 같은 규칙.

      td[0] 키워드 + (태그사전)/(추천) 라벨   td[1] 금지어   td[2] 사용여부
      td[3..] 조회수들 (마지막이 합계)
    """
    body = _tbody(html, tid)
    if not body:
        return []

    out = []
    for tr in _RE_TR.findall(body):
        cb = _RE_CB.search(tr)
        if not cb:
            continue
        cells = _RE_CELL.findall(tr)
        if not cells:
            continue

        mv = _RE_VAL.search(cb.group(0))
        name = (mv.group(1) if mv else "").strip()
        if not name:
            name = re.sub(r"\(.*?\)", "", _text(cells[0])).strip()
        if not name:
            continue

        label = _text(cells[0])
        has_dict = "태그사전" in label
        has_rec = "추천" in label
        prio = 0
        if is_tag:
            prio = 3 if (has_dict and has_rec) else 2 if has_rec else 1 if has_dict else 0

        mb = _RE_BANCATE.search(cb.group(0))
        banned = (mb.group(1).strip() if mb else "") or _text(cells[1] if len(cells) > 1 else "")
        used = _text(cells[2]) if len(cells) > 2 else ""

        views = 0
        for k in range(len(cells) - 1, 2, -1):
            nm = _RE_NUM.search(_text(cells[k]))
            if nm:
                try:
                    views = int(nm.group(0).replace(",", ""))
                except ValueError:
                    views = 0
                break

        out.append({"name": name, "prio": prio, "views": views, "tag": is_tag,
                    "banned": banned, "used": used,
                    "dict": has_dict, "rec": has_rec})
    return out


# ------------------------------------------------------------------ 조회

def fetch_attr(session, no, timeout=60) -> dict:
    """attr 팝업 -> 원상품명 / 이미지 / 분석완료 / 카테고리 저장여부"""
    from .analysis import parse_popup

    r = session.get(URL_ATTR.format(no=no), timeout=timeout)
    r.encoding = "utf-8"
    html = r.text
    info = parse_popup(html)          # token/ip/uid/product_code/analysis_date
    info["product_no"] = str(no)

    m = _RE_ETCCAT.search(html)
    cat = ""
    if m:
        cat = next((g for g in m.groups() if g), "") or ""
    info["etc_category"] = cat
    info["category_saved"] = bool(cat)

    # 원상품명 : '원상품명' 라벨이 있는 행의 다른 칸
    pname = ""
    for tr in _RE_TR.findall(html):
        cells = [_text(c) for c in _RE_CELL.findall(tr)]
        if any(c == "원상품명" for c in cells):
            for c in cells:
                if c and c != "원상품명":
                    pname = c
                    break
            break
    info["product_name"] = pname

    # 이미지 : 큰 것 우선이 아니라 등장 순 (HTTP 로는 크기를 모른다)
    imgs, seen = [], set()
    for m in re.finditer(r'<img[^>]+src=["\'](http[^"\']+)["\']', html):
        u = m.group(1)
        if u not in seen and not u.lower().endswith((".gif",)):
            seen.add(u)
            imgs.append(u)
    info["images"] = imgs[:5]
    return info


def fetch_saved_tabs(session, no, timeout=60) -> dict:
    """탭별 저장 여부 {'tag': True, 'product': False, ...}"""
    r = session.get(URL_SHELL.format(no=no), timeout=timeout)
    r.encoding = "utf-8"
    m = _RE_SAVED.search(r.text)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


def fetch_tag_rows(session, no, timeout=60) -> list:
    r = session.get(URL_TAG.format(no=no), timeout=timeout)
    r.encoding = "utf-8"
    return parse_keyword_rows(r.text, "tbody-tag", is_tag=True)


def fetch_title_rows(session, no, n=1, timeout=60) -> list:
    url = (URL_TITLE1.format(no=no) if n <= 1
           else URL_TITLEN.format(n=n, no=no))
    r = session.get(url, timeout=timeout)
    r.encoding = "utf-8"
    return parse_keyword_rows(r.text, "tbody-title", is_tag=False)

# 저장된 태그는 탭 HTML 안의 savedTagsJson 에 들어 있다.
_RE_SAVED_TAGS = re.compile(r"savedTagsJson\s*=\s*'(.*?)';", re.S)

MAX_TAGS = 10          # 사이트의 MAX_TAGS 와 같다


def parse_saved_tags(html: str) -> list:
    """탭 HTML -> 지금 저장돼 있는 태그 [{text, code}, ...]."""
    m = _RE_SAVED_TAGS.search(html)
    if not m:
        return []
    raw = m.group(1)
    # 자바스크립트 문자열 안에 있어 따옴표가 이스케이프돼 있다.
    raw = raw.replace(chr(92) + '"', '"').replace(chr(92) + "'", "'")
    try:
        return json.loads(raw)
    except ValueError:      # JSON 이 아니면 빈 목록. 그 외 예외는 덮지 않는다.
        return []


def fetch_saved_tags(session, no, timeout=60) -> list:
    r = session.get(URL_TAG.format(no=no), timeout=timeout)
    r.encoding = "utf-8"
    if "loginForm" in r.text:
        raise RuntimeError("세션 만료")
    return parse_saved_tags(r.text)


def save_tags(session, no, tags: list, timeout=60) -> dict:
    """
    태그 저장. 화면의 onTagSave() 를 그대로 재현한다.

        POST <태그 탭 URL>   mode=save   data=[{"text":..,"code":..}]

    code 는 사이트가 tag_search 로 받아둔 검색코드다(-1/-2 는 검색에 안 잡힘).
    이미 검증된 태그를 다른 L코드로 옮겨 담는 경우에는 그 code 를 그대로
    실어 보내면 되므로 tag_search 를 다시 부를 필요가 없다.

    카테고리가 저장돼 있어야 태그 탭 자체가 열린다(없으면 500).
    """
    clean = []
    for t in tags or []:
        text = (t.get("text") or "").strip() if isinstance(t, dict) else str(t).strip()
        if not text:
            continue
        code = t.get("code") if isinstance(t, dict) else None
        clean.append({"text": text, "code": code if code is not None else -2})
    if not clean:
        raise ValueError("저장할 태그가 없습니다.")
    if len(clean) > MAX_TAGS:
        clean = clean[:MAX_TAGS]

    url = URL_TAG.format(no=no)
    r = session.post(url, data={"mode": "save",
                                "data": json.dumps(clean, ensure_ascii=False)},
                     timeout=timeout)
    r.encoding = "utf-8"
    if "loginForm" in r.text:
        raise RuntimeError("세션 만료")
    # 응답은 탭 HTML 이 다시 그려진 것이다. 저장된 태그를 되읽어 확인한다.
    got = parse_saved_tags(r.text)
    return {"ok": len(got) == len(clean),
            "sent": clean, "saved": got, "http": r.status_code}

# 태그 탭 안의 onTagSearch() 가 쓰는 값들. 분석 API 와 같은 3403 서버다.
_RE_TS_TOKEN = re.compile(r'var\s+token\s*=\s*"([^"]*)"')
_RE_TS_IP = re.compile(r'var\s+ip\s*=\s*"([^"]*)"')
_RE_TS_UID = re.compile(r'var\s+uid\s*=\s*"([^"]*)"')
_RE_TS_CID = re.compile(r'var\s+commerce_id\s*=\s*"([^"]*)"')


def tag_context(html: str) -> dict:
    """태그 탭 HTML 에서 tag_search 에 필요한 값을 뽑는다."""
    def g(rx):
        m = rx.search(html)
        return m.group(1) if m else ""
    return {"token": g(_RE_TS_TOKEN), "ip": g(_RE_TS_IP),
            "uid": g(_RE_TS_UID), "commerce_id": g(_RE_TS_CID)}


def tag_search(session, no, tags: list, timeout=60) -> dict:
    """
    태그 검증. 화면의 '검색에 적용되는 태그 확인' 버튼과 같은 요청이다.

        POST http://<ip>:3403/ss_site/tag_search
             token, uid, commerce_id, tag(쉼표로 이은 목록)

    반환 {'ok': [{text, code}], 'x': [...], 'restricted': [...]}
      ok         태그사전에 있는 것. code 가 검색코드다
      x          사전에 없는 것. code = -1 (검색에는 안 잡히지만 저장은 된다)
      restricted 태그로 등록할 수 없는 단어. 저장에서 빼야 한다

    새로 만든 키워드(데이터랩 등)를 태그로 넣으려면 이 단계를 반드시 거쳐야
    code 를 얻는다. 이미 저장된 태그를 옮길 때는 code 가 이미 있어 불필요하다.
    """
    words = [str(t).strip() for t in (tags or []) if str(t).strip()]
    if not words:
        return {"ok": [], "x": [], "restricted": []}

    r = session.get(URL_TAG.format(no=no), timeout=timeout)
    r.encoding = "utf-8"
    ctx = tag_context(r.text)
    if not ctx["token"] or not ctx["ip"]:
        raise RuntimeError("태그 탭에서 token/ip 를 찾지 못했습니다 "
                           "(카테고리가 저장돼 있어야 탭이 열립니다)")

    resp = session.post(
        f"http://{ctx['ip']}:3403/ss_site/tag_search",
        data={"token": ctx["token"], "uid": ctx["uid"],
              "commerce_id": ctx["commerce_id"], "tag": ",".join(words)},
        timeout=timeout)
    resp.encoding = "utf-8"
    d = json.loads(resp.text)
    if d.get("msg") == "error":
        raise RuntimeError(f"tag_search 오류: {str(d.get('result'))[:120]}")
    res = d.get("result") or {}

    def norm(items, default_code):
        out = []
        for it in items or []:
            if isinstance(it, dict):
                text = it.get("text") or ""
                code = it.get("code") or it.get("id") or default_code
            else:
                text, code = str(it), default_code
            if text:
                out.append({"text": text, "code": code})
        return out

    return {"ok": norm(res.get("dicO"), -2),
            "x": norm(res.get("dicX"), -1),
            "restricted": norm(res.get("restricted"), -2)}

def urls(no) -> dict:
    """
    상품(product_no) 하나의 화면 주소. 사람이 브라우저로 열어 고칠 때 쓴다.

    태그·상품명은 같은 팝업의 탭이라 해시(#tag / #product)로 갈린다.
    'OF' 는 attr 팝업이다 — 카테고리·속성·분석 상태가 여기 있다.
    """
    no = str(no)
    shell = URL_SHELL.format(no=no)
    return {"tag": shell + "#tag",
            "product": shell + "#product",
            "of": URL_ATTR.format(no=no)}
