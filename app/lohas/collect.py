"""
LCP 단위 키워드·카테고리 수집.

로하스 화면 조작 순서는
  상품정보 → [포함상품보기] → 옵션별 요약정보(선택01~20 제품명)
            [키워드관리]   → 지마켓용 키워드 200~500개
인데, 두 팝업 모두 서버가 그려서 내려주므로 브라우저 없이 HTTP 로 읽는다.

  포함상품보기 : /commercial/commercial_prodtieEdit4/popup/ok/product_id/{pid}
      - 옵션 제품명이 JS 배열로 들어있다:  Arr1[0] = "01.조리기구 스탠드 ...";
      - 희망검색어, 마켓별 카테고리(옥션/G마켓/인터파크/스토어팜/11번가),
        상품명, 원산지, 브랜드, 제조사, 판매원가도 같은 페이지에 있다
  키워드관리   : /commercial/commercial_keyword_auto2/popup/ok/product_id/{pid}
                 /product_code/{LCP}/fc/A001/fv/{cate}/orderby/keyword
      - 추천키워드(체크박스 + 옥션/지마켓 조회수), 사용키워드 목록
  카테고리     : /ajax_ss_attr/rmt/ok/mode/prod_category/code/{LCP}  (JSON)
"""
import html as _html
import json
import re
import time
import urllib.parse

from . import constants as C

MANAGER = C.BASE + "/manager/commercial"
URL_TIE = MANAGER + "/commercial_prodtieEdit4/popup/ok/product_id/{pid}"
URL_KW = (MANAGER + "/commercial_keyword_auto2/popup/ok/product_id/{pid}"
          "/product_code/{lcp}/fc/A001/fv/{cate}/orderby/keyword")
URL_CAT = (C.BASE + "/manager/commercial/ajax_ss_attr/rmt/ok/mode/"
           "prod_category/code/{lcp}")

_RE_ARR1 = re.compile(r'Arr1\[(\d+)\]\s*=\s*"((?:[^"\\]|\\.)*)"')
_RE_ARR2 = re.compile(r'Arr2\[(\d+)\]\[(\d+)\]\s*=\s*"((?:[^"\\]|\\.)*)"')
_RE_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_RE_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_RE_CB = re.compile(r'<input[^>]*type=["\']?checkbox[^>]*>', re.S)
_RE_VAL = re.compile(r'value=["\']([^"\']*)["\']')
_RE_NUM = re.compile(r"\d[\d,]*")
_RE_EDITP4 = re.compile(r"editProduct4\((\d+)\)")


def _text(h: str) -> str:
    t = re.sub(r"<[^>]+>", " ", h or "")
    return " ".join(_html.unescape(t).replace("\xa0", " ").split())


def _rows(html: str) -> list:
    """<tr> 를 [셀텍스트...] 로."""
    return [[_text(c) for c in _RE_CELL.findall(tr)] for tr in _RE_TR.findall(html)]


def find_product_id(attr_html: str) -> str:
    """attr 팝업 HTML 에서 editProduct4(pid) 의 pid 를 뽑는다."""
    m = _RE_EDITP4.search(attr_html or "")
    return m.group(1) if m else ""


# ------------------------------------------------------------------ 포함상품

def parse_tie(html: str) -> dict:
    """포함상품보기 페이지 파싱."""
    out = {"product_name": "", "wish_keywords": [], "options": [],
           "markets": {}, "origin": "", "brand": "", "maker": "", "cost": ""}

    # ---- 옵션 제품명 (선택01~20) ----
    opts = {}
    for m in _RE_ARR1.finditer(html):
        idx = int(m.group(1))
        name = _html.unescape(m.group(2)).replace('\\"', '"').strip()
        if name:
            opts[idx] = name
    subs = {}
    for m in _RE_ARR2.finditer(html):
        i, j = int(m.group(1)), int(m.group(2))
        v = _html.unescape(m.group(3)).replace('\\"', '"').strip()
        if j > 0 and v:                       # [i][0] 은 제품명 자신
            subs.setdefault(i, []).append(v)
    for i in sorted(opts):
        out["options"].append({"seq": i + 1, "name": opts[i],
                               "subs": subs.get(i, [])})

    # ---- 표에서 라벨-값 뽑기 ----
    label_map = {"상품명": "product_name", "원산지": "origin", "브랜드": "brand",
                 "제조사": "maker", "판매원가": "cost"}
    for cells in _rows(html):
        if len(cells) < 2:
            continue
        key = cells[0].strip()
        val = cells[1].strip()
        if key in label_map and not out[label_map[key]]:
            out[label_map[key]] = val
        elif key == "희망검색어" and val:
            out["wish_keywords"] = [w for w in val.split() if w]
        elif key == "카테고리" and val:
            # '옥션 : a > b  G마켓 : c > d ...' -> 마켓명 위치로 잘라낸다
            names = ["옥션", "G마켓", "지마켓", "인터파크", "스토어팜", "11번가"]
            pat = re.compile(r"(" + "|".join(map(re.escape, names)) + r")\s*:")
            hits = list(pat.finditer(val))
            for i, mm in enumerate(hits):
                end = hits[i + 1].start() if i + 1 < len(hits) else len(val)
                path = val[mm.end():end].strip()
                if path:
                    out["markets"][mm.group(1)] = path
    return out


def fetch_tie(session, product_id, timeout=60) -> dict:
    r = session.get(URL_TIE.format(pid=product_id), timeout=timeout)
    r.encoding = "utf-8"
    if "loginForm" in r.text:
        raise RuntimeError("세션 만료")
    d = parse_tie(r.text)
    d["_bytes"] = len(r.text)
    return d


# ------------------------------------------------------------------ 키워드관리

_RE_FK = re.compile(
    r'<textarea[^>]*name=["\']fk["\'][^>]*>(.*?)</textarea>', re.S)
_RE_USED_LINE = re.compile(r"^(.+?)(?:\(\s*([\d,]+)\s*\))?$")


def parse_used(html: str) -> list:
    """
    사용키워드 목록. `<textarea name="fk">` 안에 한 줄에 하나씩 들어있고,
    조회수가 있으면 '키워드    ( 1,479 )' 형태로 붙는다.
    """
    m = _RE_FK.search(html)
    if not m:
        return []
    raw = _html.unescape(m.group(1))
    out, seen = [], set()
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        mm = _RE_USED_LINE.match(line)
        if not mm:
            continue
        kw = mm.group(1).strip()
        views = mm.group(2)
        if not kw or kw in seen:
            continue
        seen.add(kw)
        out.append({"keyword": kw,
                    "views": int(views.replace(",", "")) if views else None})
    return out


def parse_keywords(html: str) -> dict:
    """
    키워드관리(희망검색어 목록) 페이지 파싱.
      추천키워드 : 체크박스 행 (옥션조회수 / 지마켓조회수 / 합계)
      사용키워드 : textarea[name=fk]  ← 지마켓용으로 등록된 키워드 (최대 500)
    """
    h = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S)
    rec = []
    for tr in _RE_TR.findall(h):
        cb = _RE_CB.search(tr)
        if not cb or "selall" in cb.group(0):
            continue
        cells = [_text(c) for c in _RE_CELL.findall(tr)]
        v = _RE_VAL.search(cb.group(0))
        name = ((v.group(1) if v else "") or (cells[0] if cells else "")).strip()
        if not name:
            continue
        nums = []
        for c in cells[1:4]:
            mm = _RE_NUM.search(c)
            nums.append(int(mm.group(0).replace(",", "")) if mm else 0)
        while len(nums) < 3:
            nums.append(0)
        rec.append({"keyword": name, "auction": nums[0],
                    "gmarket": nums[1], "total": nums[2]})

    used = parse_used(html)
    m = re.search(r"사용키워드\s*\(\s*(\d+)\s*개", _text(h))
    return {"recommend": rec, "used": used,
            "used_count": int(m.group(1)) if m else len(used)}


def fetch_keywords(session, product_id, lcp, cate="", timeout=90) -> dict:
    url = URL_KW.format(pid=product_id, lcp=urllib.parse.quote(lcp),
                        cate=urllib.parse.quote(cate or ""))
    r = session.get(url, timeout=timeout)
    r.encoding = "utf-8"
    if "loginForm" in r.text:
        raise RuntimeError("세션 만료")
    d = parse_keywords(r.text)
    d["_bytes"] = len(r.text)
    return d


# ------------------------------------------------------------------ 상품명 토큰

_RE_SEQ_PREFIX = re.compile(r"^\s*\d{1,3}\s*[.)]\s*")


def strip_seq(name: str) -> str:
    """옵션 제품명 앞의 '01.' 순번을 떼어낸다."""
    return _RE_SEQ_PREFIX.sub("", name or "").strip()


def title_tokens(options: list, min_len: int = 2) -> list:
    """
    옵션 제품명을 띄어쓰기 단위로 쪼개 상품명 키워드 후보를 만든다.

    '01.K2 마술 저금통 만들기 4인' -> K2 / 마술 / 저금통 / 만들기 / 4인
    같은 LCP 안에서 여러 옵션에 나온 토큰일수록 그 상품을 대표한다고 보고
    등장 횟수(freq)를 함께 센다.

    반환: [{token, freq}] — 등장 많은 순
    """
    from collections import Counter

    cnt = Counter()
    for o in options or []:
        name = strip_seq(o.get("name") if isinstance(o, dict) else o)
        seen = set()
        for w in name.split():
            w = w.strip(" .,/()[]{}·~-_+")
            if len(w) < min_len or w in seen:
                continue
            seen.add(w)
            cnt[w] += 1
    return [{"token": k, "freq": v}
            for k, v in sorted(cnt.items(), key=lambda x: (-x[1], x[0]))]


# ------------------------------------------------------------------ 카테고리

def fetch_categories(session, lcp, timeout=60) -> list:
    r = session.get(URL_CAT.format(lcp=urllib.parse.quote(lcp)), timeout=timeout)
    r.encoding = "utf-8"
    try:
        data = json.loads(r.text)
    except Exception:
        return []
    out = []
    for x in data or []:
        out.append({"code": str(x.get("code") or ""),
                    "name": x.get("name") or "",
                    "cnt": int(x.get("cnt") or 0),
                    "unit": x.get("unit"), "capacity": x.get("capacity")})
    out.sort(key=lambda c: -c["cnt"])
    return out


# ------------------------------------------------------------------ 통합

def collect_one(client, lcp: str, product_no: str, log=print) -> dict:
    """
    LCP 1건 수집. product_no 는 목록의 attr 팝업 no.
    반환: {lcp, product_id, tie, keywords, categories, elapsed_sec}
    """
    from . import tabs

    t0 = time.time()
    # attr 팝업에서 product_id 확보
    r = client.session.get(tabs.URL_ATTR.format(no=product_no), timeout=60)
    r.encoding = "utf-8"
    pid = find_product_id(r.text)
    if not pid:
        raise RuntimeError("product_id 를 찾지 못했습니다 (editProduct4)")

    tie = fetch_tie(client.session, pid)
    log(f"  [포함상품] 옵션 {len(tie['options'])}개 / 희망검색어 "
        f"{len(tie['wish_keywords'])}개 / 마켓 {len(tie['markets'])}곳")

    kw = fetch_keywords(client.session, pid, lcp)
    log(f"  [키워드관리] 추천 {len(kw['recommend'])}개 / 사용 {len(kw['used'])}개")

    cats = fetch_categories(client.session, lcp)
    log(f"  [카테고리] {len(cats)}개 (1순위 {cats[0]['name'][:30] if cats else '-'})")

    toks = title_tokens(tie.get("options"))
    log(f"  [상품명토큰] {len(toks)}개 (최다 "
        f"{toks[0]['token'] if toks else '-'}×{toks[0]['freq'] if toks else 0})")

    return {"lcp_code": lcp, "product_id": pid, "product_no": str(product_no),
            "tie": tie, "keywords": kw, "categories": cats,
            "title_tokens": toks,
            "elapsed_sec": round(time.time() - t0, 1)}
