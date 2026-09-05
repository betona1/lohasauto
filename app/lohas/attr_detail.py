"""
L코드(상품) 단위 작업 상태 상세.

attr 팝업 한 번이면 그 상품의 작업 현황이 전부 나온다. 페이지가 스스로
아래 값으로 세 줄(카테고리 / 속성 / 상품명·태그)의 저장 여부를 판정한다.

    var etc_category  = 50004771;                  // 카테고리 (없으면 "")
    var etc_attribute = [];                        // 속성
    var etc_titles    = ["냄비 뚜껑거치대 ...", "", "", "", ""];   // 상품명1~5
    var etc_tag       = [...];                     // 태그
    var analysis_date = "2026-08-20 17:07:49";     // 상품분석 완료

    if (etc_category) savedStatus[0] = true;                       // 카테고리
    if (etc_attribute.length > 0) savedStatus[1] = true;           // 속성
    if (etc_titles[0] && etc_tag.length > 0) savedStatus[2] = true; // 상품명/태그
"""
import json
import re
import time

from . import tabs

_RE_CAT = re.compile(
    r'(?:var|let|const)\s+etc_category\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(\d+))')
_RE_ADATE = re.compile(r'(?:var|let)\s+analysis_date\s*=\s*"([^"]*)"')


def _js_array(html: str, name: str) -> list:
    """`var name = [ ... ];` 를 파이썬 리스트로."""
    m = re.search(r'(?:var|let|const)\s+' + name + r'\s*=\s*(\[)', html)
    if not m:
        return []
    i = m.end() - 1
    depth, j, instr, q = 0, i, False, ""
    while j < len(html):
        ch = html[j]
        if instr:
            if ch == "\\":
                j += 2
                continue
            if ch == q:
                instr = False
        elif ch in "\"'":
            instr, q = True, ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                break
        j += 1
    raw = html[i:j + 1]
    try:
        return json.loads(raw)
    except Exception:
        # 작은따옴표 등으로 JSON 이 아닐 때는 문자열만 긁는다
        return [x for x in re.findall(r'"([^"]*)"|\'([^\']*)\'', raw)
                for x in x if x]


def parse_attr_detail(html: str) -> dict:
    """attr 팝업 HTML -> 작업 상태."""
    m = _RE_CAT.search(html)
    cat = ""
    if m:
        cat = next((g for g in m.groups() if g), "") or ""

    titles = [t for t in _js_array(html, "etc_titles")]
    attribute = _js_array(html, "etc_attribute")
    tag = _js_array(html, "etc_tag")

    m = _RE_ADATE.search(html)
    adate = m.group(1) if m else ""

    cat_saved = bool(cat)
    attr_saved = len(attribute) > 0
    title_saved = bool(titles and titles[0]) and len(tag) > 0

    return {
        "etc_category": cat,
        "analysis_date": adate,
        "analysis_done": adate.startswith("20"),
        "cat_saved": cat_saved,
        "attr_saved": attr_saved,
        "title_saved": title_saved,
        "titles": titles,
        "title_count": len([t for t in titles if t]),
        "title1": titles[0] if titles else "",
        "tags": tag,
        "tag_count": len(tag),
        "attribute_count": len(attribute),
        # 다음에 해야 할 작업 (사이트가 강제하는 순서 기준)
        "next_step": ("상품분석" if not adate.startswith("20")
                      else "카테고리" if not cat_saved
                      else "상품명/태그" if not title_saved
                      else "완료"),
    }


def fetch_detail(session, product_no, timeout=40) -> dict:
    r = session.get(tabs.URL_ATTR.format(no=product_no), timeout=timeout)
    r.encoding = "utf-8"
    if "loginForm" in r.text:
        raise RuntimeError("세션 만료")
    d = parse_attr_detail(r.text)
    d["product_no"] = str(product_no)
    return d


def collect_folder(client, rows: list, log=print, progress=None,
                   should_stop=None, delay: float = 0.0) -> dict:
    """
    L코드 목록(rows: db.lcode_rows 결과)을 돌며 상태를 모은다.
    반환: {'rows': [...], 'ok', 'fail', 'elapsed_sec'}
    """
    t0 = time.time()
    out, fail = [], 0
    total = len(rows)

    for i, r in enumerate(rows, 1):
        if should_stop and should_stop():
            log("[상세] 사용자 중단")
            break
        no = r.get("product_no")
        if not no:
            fail += 1
            continue
        try:
            d = fetch_detail(client.session, no)
            d["lcp_code"] = r["lcp_code"]
            d["l_code"] = r["l_code"]
            out.append(d)
        except Exception as e:
            fail += 1
            if fail <= 5:
                log(f"  ! {r.get('l_code')} 실패: {str(e)[:60]}")
        if progress:
            progress(i, total)
        if i % 200 == 0:
            log(f"  {i:,}/{total:,} ({time.time() - t0:.0f}초)")
        if delay:
            time.sleep(delay)

    el = round(time.time() - t0, 1)
    log(f"[상세] 완료 {len(out):,}건 / 실패 {fail}건 ({el}초)")
    return {"rows": out, "ok": len(out), "fail": fail, "elapsed_sec": el}
