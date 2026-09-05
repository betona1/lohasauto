"""
상품정보관리(commercial_ss_image) 폴더 수량 점검.

작업폴더 하나를 선택해 검색한 뒤, 결과 그리드를 전 페이지 순회하면서
행별로 '대표이미지' / '상품정보' 상태를 읽어 집계한다.

상태 판정은 2단계다.
  1) 셀 텍스트에 '승인완료 / 미작업 / 작업중' 같은 단어가 있으면 그걸 사용
  2) 텍스트가 없으면 LOHASPIC 과 동일하게 글자색으로 판정
     (파란색 = 완료, 주황/빨강 = 미작업)
"""
import re
import time
from datetime import datetime

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from . import constants as C
from .browser import (
    accept_all_alerts,
    describe,
    enter_content_frame,
)
from .folders import select_folder, select_view_count

# 행에서 '수정' 버튼을 찾는 XPath (데이터행 판별용)
EDIT_BTN_XPATH = (
    ".//input[contains(@value,'수정')] "
    "| .//a[contains(.,'수정')] "
    "| .//button[contains(.,'수정')]"
)

LCP_RE = re.compile(r"LCP[_A-Z0-9]+")
LCODE_RE = re.compile(r"\bL\d{4,}\b")


# ------------------------------------------------------------------ 색상 판정

def _rgb(css_color: str):
    if not css_color:
        return None
    nums = re.findall(r"\d+", css_color)
    if len(nums) < 3:
        return None
    if len(nums) >= 4 and nums[3] == "0":   # 투명
        return None
    return int(nums[0]), int(nums[1]), int(nums[2])


def is_blue(css_color: str) -> bool:
    c = _rgb(css_color)
    if not c:
        return False
    r, g, b = c
    return b >= 120 and b > r + 40 and b > g + 40


def is_orange(css_color: str) -> bool:
    c = _rgb(css_color)
    if not c:
        return False
    r, g, b = c
    return r >= 150 and b <= 120 and r > b + 60 and g < r - 30


def cell_color_kind(el) -> tuple:
    """셀(및 내부 a/font/span/b/input)의 색 → ('orange'|'blue'|'other', 색문자열)"""
    driver = getattr(el, "parent", None)
    colors = []
    for prop in ("color", "background-color"):
        try:
            colors.append(el.value_of_css_property(prop) or "")
        except Exception:
            pass

    try:
        if driver is not None:
            driver.implicitly_wait(0)
        children = el.find_elements(
            By.XPATH, ".//a | .//font | .//span | .//b | .//u | .//input"
        )
    except Exception:
        children = []
    finally:
        if driver is not None:
            driver.implicitly_wait(10)

    for c in children:
        for prop in ("color", "background-color"):
            try:
                colors.append(c.value_of_css_property(prop) or "")
            except Exception:
                continue

    shown = " / ".join(dict.fromkeys(c for c in colors if c)) or "?"

    for c in colors:
        if is_orange(c):
            return "orange", shown
    for c in colors:
        if is_blue(c):
            return "blue", shown
    return "other", shown


# ------------------------------------------------------------------ 상태 보조판정
#
# 이 페이지의 상태는 버튼 CSS 클래스로만 표현되고 2진값이다.
#   btn_m* = 최종완료(대표이미지 승인완료 / 상품정보 저장완료)
#   btn_z* = 그 외(미작업, 이미지작업, 제외, 보류 ...)
# 세부 상태 구분은 불가능하므로 정확한 집계는 필터 검색(search_cell)으로 한다.
# 아래 함수는 덤프/진단 표시용 보조 판정이다.

def class_status(css_class: str) -> str:
    """버튼 class -> '완료' / '미완료' / '알수없음'"""
    c = (css_class or "").strip()
    if not c:
        return "알수없음"
    if C.CLASS_DONE_PREFIX in c:
        return "완료"
    if C.CLASS_TODO_PREFIX in c:
        return "미완료"
    return "알수없음"


def cell_status(cell) -> tuple:
    """셀 안 버튼의 (상태, class문자열)"""
    try:
        btn = cell.find_element(By.CSS_SELECTOR, "input,button,a")
        cls = (btn.get_attribute("class") or "").strip()
    except Exception:
        return "알수없음", ""
    return class_status(cls), cls


# ------------------------------------------------------------------ 그리드 읽기

def header_map(driver) -> dict:
    """헤더명 -> 컬럼 index"""
    colmap = {}
    try:
        driver.implicitly_wait(0)
        cells = []
        for css in C.GRID_HEADER_SELECTORS:
            cells = driver.find_elements(By.CSS_SELECTOR, css)
            if cells:
                break
        for i, c in enumerate(cells):
            name = (c.text or "").strip().replace(" ", "").replace("\n", "")
            if name:
                colmap.setdefault(name, i)
    except Exception:
        pass
    finally:
        driver.implicitly_wait(10)
    return colmap


def result_rows(driver) -> list:
    """'수정' 버튼이 있는 데이터 행만 추린다."""
    try:
        driver.implicitly_wait(0)
        for css in C.GRID_ROW_SELECTORS:
            data_rows = []
            for r in driver.find_elements(By.CSS_SELECTOR, css):
                try:
                    if r.find_elements(By.XPATH, EDIT_BTN_XPATH):
                        data_rows.append(r)
                except Exception:
                    continue
            if data_rows:
                return data_rows
    finally:
        driver.implicitly_wait(10)
    return []


def _col_index(colmap: dict, names) -> int:
    for n in names:
        if n in colmap:
            return colmap[n]
    # 부분 일치 폴백
    for n in names:
        for key, idx in colmap.items():
            if n in key:
                return idx
    return -1


def extract_row(row, colmap: dict, page_no: int, row_no: int) -> dict:
    """행 하나에서 코드/상태 추출."""
    info = {
        "page_no": page_no,
        "row_no": row_no,
        "lcp_code": "",
        "l_code": "",
        "product_name": "",
        "img_status": "기타",
        "img_raw": "",
        "info_status": "기타",
        "info_raw": "",
        "is_target": 0,
    }

    try:
        tds = row.find_elements(By.TAG_NAME, "td")
    except Exception:
        return info

    texts = []
    for t in tds:
        try:
            texts.append(" ".join((t.text or "").split()))
        except Exception:
            texts.append("")

    joined = " ".join(texts)

    # ---- 코드 : 헤더 우선, 없으면 정규식으로 전체 행에서 추출 ----
    i = _col_index(colmap, C.COL_LCP)
    if 0 <= i < len(texts):
        m = LCP_RE.search(texts[i].upper())
        info["lcp_code"] = m.group(0) if m else texts[i]
    else:
        m = LCP_RE.search(joined.upper())
        info["lcp_code"] = m.group(0) if m else ""

    i = _col_index(colmap, C.COL_LCODE)
    if 0 <= i < len(texts):
        m = LCODE_RE.search(texts[i].upper())
        info["l_code"] = m.group(0) if m else texts[i]
    else:
        m = LCODE_RE.search(joined.upper())
        info["l_code"] = m.group(0) if m else ""

    i = _col_index(colmap, C.COL_NAME)
    if 0 <= i < len(texts):
        info["product_name"] = texts[i][:400]

    # ---- 대표이미지 / 상품정보 상태 (버튼 class 기반 2진 판정) ----
    i = _col_index(colmap, C.COL_IMAGE)
    if 0 <= i < len(tds):
        info["img_status"], info["img_raw"] = cell_status(tds[i])

    i = _col_index(colmap, C.COL_INFO)
    if 0 <= i < len(tds):
        info["info_status"], info["info_raw"] = cell_status(tds[i])

    # 참고용 표식 (정확한 작업대상 집계는 filter 검색으로 산출)
    info["is_target"] = int(
        info["img_status"] == "완료" and info["info_status"] == "미완료"
    )
    return info


# ------------------------------------------------------------------ 페이징

def page_signature(driver, colmap: dict) -> str:
    rows = result_rows(driver)
    if not rows:
        return ""
    try:
        first = extract_row(rows[0], colmap, 0, 0)
        last = extract_row(rows[-1], colmap, 0, 0)
        return f"{len(rows)}:{first['lcp_code']}:{last['lcp_code']}"
    except Exception:
        return str(len(rows))


def goto_next_page(driver, current_page: int, log=print) -> bool:
    """다음 페이지 번호 링크 우선, 없으면 '다음' 링크."""
    next_no = str(current_page + 1)
    try:
        driver.implicitly_wait(0)
        links = driver.find_elements(By.TAG_NAME, "a")

        for a in links:
            try:
                if (a.text or "").strip() == next_no and a.is_displayed():
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", a)
                    a.click()
                    log(f"[페이징] {next_no}페이지 이동")
                    return True
            except Exception:
                continue

        for a in links:
            try:
                t = (a.text or "").strip()
                label = f"{t} {a.get_attribute('title') or ''} {a.get_attribute('alt') or ''}"
                if t in (">>", "»", "맨끝", "끝"):
                    continue
                if "다음" in label or t in (">", "▶", "next", "Next"):
                    if not a.is_displayed():
                        continue
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", a)
                    a.click()
                    log(f"[페이징] {next_no}페이지 이동('다음')")
                    return True
            except Exception:
                continue
    finally:
        driver.implicitly_wait(10)
    return False


# ------------------------------------------------------------------ 검색

def find_search_input(driver):
    candidates = [
        (By.CSS_SELECTOR, "#searchBox textarea"),
        (By.TAG_NAME, "textarea"),
        (By.CSS_SELECTOR, "#searchBox input[type='text']"),
        (By.CSS_SELECTOR, "input[type='text']"),
    ]
    try:
        driver.implicitly_wait(0)
        for by, sel in candidates:
            for el in driver.find_elements(by, sel):
                try:
                    if el.is_displayed() and el.is_enabled():
                        return el
                except Exception:
                    continue
    finally:
        driver.implicitly_wait(10)
    return None


def click_search(driver, search_el=None, log=print) -> bool:
    """'검색' 버튼 클릭. 못 찾으면 엔터. (LOHASPIC 동일)"""
    try:
        driver.implicitly_wait(0)
        buttons = driver.find_elements(
            By.CSS_SELECTOR,
            "#searchBox button, #searchBox input[type='submit'], "
            "#searchBox input[type='button'], #searchBox input[type='image'], "
            "button, input[type='submit'], input[type='button'], input[type='image']",
        )
        for b in buttons:
            try:
                if not b.is_displayed():
                    continue
                label = " ".join((
                    (b.get_attribute("value") or "") + " "
                    + (b.get_attribute("alt") or "") + " "
                    + (b.text or "")
                ).split())
                if "검색" in label and "초기화" not in label:
                    log(f"[검색] 버튼 클릭 → {describe(b)}")
                    b.click()
                    return True
            except Exception:
                continue

        for b in driver.find_elements(By.CSS_SELECTOR, "#searchBox button"):
            try:
                if b.is_displayed():
                    b.click()
                    log("[검색] #searchBox 첫 버튼 클릭")
                    return True
            except Exception:
                continue
    finally:
        driver.implicitly_wait(10)

    if search_el is not None:
        try:
            search_el.send_keys(Keys.ENTER)
            log("[검색] 버튼 못 찾음 → 엔터")
            return True
        except Exception:
            pass
    return False


def open_folder_search(driver, folder_name: str, page_size: str, log=print) -> None:
    """ss_image 페이지 이동 → 폴더 선택 → 개수 설정 → 검색."""
    driver.get(C.SS_IMAGE_URL)
    time.sleep(1)
    accept_all_alerts(driver)
    enter_content_frame(driver)

    if not select_folder(driver, folder_name, log=log):
        raise RuntimeError(
            f"'{folder_name}' 작업폴더를 페이지에서 찾지 못했습니다.\n"
            "폴더 목록을 다시 스캔해보세요."
        )
    time.sleep(0.5)

    select_view_count(driver, page_size, log=log)
    time.sleep(0.3)

    # 검색어 칸은 비워둔다 (폴더 전체 조회)
    search_el = find_search_input(driver)
    if search_el is not None:
        try:
            search_el.clear()
        except Exception:
            pass

    click_search(driver, search_el, log=log)
    time.sleep(1.5)
    accept_all_alerts(driver)


# ------------------------------------------------------------------ 점검 본체
#
# 이 페이지는 상태를 셀 텍스트로 주지 않고 버튼 CSS 클래스(btn_m*/btn_z*)로만
# 표시하는데 그 값이 2진값이라 '미작업 vs 이미지작업', '미작업 vs 제외 vs 보류'
# 를 구분할 수 없다. 게다가 URL 페이징(/p/N)이 동작하지 않아 한 번에 최대
# 1000행까지만 읽힌다.
#
# 그래서 화면의 상태 필터 콤보를 직접 걸어 검색하고 결과 행수를 세는 방식을 쓴다.
# 대표이미지 3상태 x 상품정보 4상태 = 12칸 매트릭스로 쪼개면 각 칸이 1000행
# 미만이 되어 정확한 집계가 가능하다.

ROWS_JS = """
const out=[];
for (const tr of document.querySelectorAll('table.grid_tbl tbody tr')) {
  const tds=tr.querySelectorAll('td');
  if (tds.length<8 || !tds[5].querySelector('input')) continue;
  out.push([tds[1].innerText.trim(), tds[2].innerText.trim()]);
}
return out;
"""


def apply_filter(driver, name: str, value: str, log=print) -> bool:
    """검색폼의 상태 콤보 하나를 지정."""
    from selenium.webdriver.support.ui import Select
    try:
        Select(driver.find_element(By.NAME, name)).select_by_value(value)
        return True
    except Exception as e:
        log(f"[필터] {name}={value} 지정 실패: {str(e)[:80]}")
        return False


def _read_rows(driver) -> list:
    """현재 결과 그리드의 (LCP코드, L코드) 목록."""
    try:
        return [tuple(r) for r in driver.execute_script(ROWS_JS)]
    except Exception:
        # JS 가 막히면 셀레늄 방식으로 폴백
        out = []
        colmap = header_map(driver)
        for i, row in enumerate(result_rows(driver)):
            info = extract_row(row, colmap, 1, i + 1)
            out.append((info["lcp_code"], info["l_code"]))
        return out


def search_cell(driver, folder_name: str, page_size: str,
                img_value: str = None, info_value: str = None,
                log=print) -> dict:
    """
    폴더 + 상태필터 조합으로 검색해 결과를 읽는다.
    결과가 상한(1000행)에 걸리면 역순으로 한 번 더 읽어 합집합을 구한다.
    반환: {'rows': [(lcp,l),...], 'capped': bool}
    """
    def run(order_value: str) -> list:
        open_folder_search(driver, folder_name, page_size, log=lambda *a, **k: None)
        if img_value:
            apply_filter(driver, C.IMAGE_FILTER_NAME, img_value, log=log)
        if info_value:
            apply_filter(driver, C.INFO_FILTER_NAME, info_value, log=log)
        if order_value:
            apply_filter(driver, C.ORDER_NAME, order_value, log=lambda *a, **k: None)
        click_search(driver, None, log=lambda *a, **k: None)
        time.sleep(1.4)
        accept_all_alerts(driver)
        return _read_rows(driver)

    rows = run(C.ORDER_ASC)
    capped = len(rows) >= C.MAX_ROWS_PER_SEARCH

    if capped:
        # 1000행을 꽉 채웠다 = 더 있다. 역순으로 뒤에서부터 1000행을 더 읽어
        # 합집합을 만들면 총 2000건까지는 정확히 셀 수 있다.
        log(f"      상한({C.MAX_ROWS_PER_SEARCH}행) 도달 → 역순 조회로 보완")
        rows_desc = run(C.ORDER_DESC)
        merged = list(dict.fromkeys(rows + rows_desc))
        if len(rows_desc) < C.MAX_ROWS_PER_SEARCH or len(merged) < len(rows) + len(rows_desc):
            capped = False   # 두 방향이 겹쳤다 = 전체를 다 봤다
        rows = merged

    return {"rows": rows, "capped": capped}


def inspect_folder(driver, folder_name: str, page_size: str = "1000",
                   max_pages: int = 100, log=print, progress=None,
                   should_stop=None, quick: bool = False) -> dict:
    """
    작업폴더 하나의 상태별 수량 점검 (필터 매트릭스 방식).

    quick=True 면 ★작업대상 한 칸(이미지승인완료 x 상품정보 미작업)만 검색한다.
    검색이 12~14회에서 1회로 줄어 14~16분 걸리던 점검이 1~2분에 끝난다.
    대신 나머지 상태별 수량은 채워지지 않는다(mode='quick').

    반환: {'summary': {...}, 'cells': [...], 'items': [...]}
    """
    started = time.time()
    scanned_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if quick:
        img_filters = [(lab, v) for lab, v in C.IMAGE_FILTERS
                       if lab == C.TARGET_IMAGE]
        info_filters = [(lab, v) for lab, v in C.INFO_FILTERS
                        if lab == C.TARGET_INFO]
        log("[점검] 빠른 점검 : 작업대상 1칸만 조회합니다.")
    else:
        img_filters = list(C.IMAGE_FILTERS)
        info_filters = list(C.INFO_FILTERS)

    cells = []
    target_rows = []
    all_rows = set()
    any_capped = False

    total_cells = len(img_filters) * len(info_filters)
    done_cells = 0

    for img_label, img_val in img_filters:
        for info_label, info_val in info_filters:
            if should_stop and should_stop():
                log("[점검] 사용자 중단")
                break

            res = search_cell(driver, folder_name, page_size,
                              img_val, info_val, log=log)
            rows = res["rows"]
            lcps = {r[0] for r in rows if r[0]}
            is_target = (img_label == C.TARGET_IMAGE and info_label == C.TARGET_INFO)

            cells.append({
                "image_status": img_label,
                "info_status": info_label,
                "row_count": len(rows),
                "lcp_count": len(lcps),
                "capped": int(res["capped"]),
                "is_target": int(is_target),
            })
            all_rows.update(rows)
            any_capped = any_capped or res["capped"]
            if is_target:
                target_rows = rows

            mark = " ★작업대상" if is_target else ""
            cap = " (상한초과)" if res["capped"] else ""
            log(f"[점검] 대표이미지={img_label} / 상품정보={info_label} "
                f"→ {len(rows)}행 / LCP {len(lcps)}종{cap}{mark}")

            done_cells += 1
            if progress:
                progress(done_cells, total_cells)

    target_lcps = {r[0] for r in target_rows if r[0]}
    all_lcps = {r[0] for r in all_rows if r[0]}

    def pick(dim, label, key):
        for c in cells:
            if c[dim] == label:
                pass
        return sum(c[key] for c in cells if c[dim] == label)

    summary = {
        "folder_name": folder_name,
        "scanned_at": scanned_at,
        "mode": "quick" if quick else "full",
        # quick 은 한 칸만 봤으므로 전체 합계를 알 수 없다 -> 0 으로 두고 UI에서 '-' 표시
        "total_rows": 0 if quick else len(all_rows),
        "total_lcps": 0 if quick else len(all_lcps),

        "img_todo_rows":  pick("image_status", "미작업", "row_count"),
        "img_work_rows":  pick("image_status", "이미지작업", "row_count"),
        "img_done_rows":  pick("image_status", "이미지승인완료", "row_count"),

        "info_todo_rows":    pick("info_status", "미작업", "row_count"),
        "info_save_rows":    pick("info_status", "저장완료", "row_count"),
        "info_exclude_rows": pick("info_status", "제외", "row_count"),
        "info_hold_rows":    pick("info_status", "보류", "row_count"),

        "target_rows": len(target_rows),
        "target_lcps": len(target_lcps),
        "capped": int(any_capped),
        "elapsed_sec": round(time.time() - started, 1),
        "note": None,
    }

    items = [
        {
            "folder_name": folder_name,
            "bucket": "target",
            "lcp_code": lcp,
            "l_code": lcode,
            "image_status": C.TARGET_IMAGE,
            "info_status": C.TARGET_INFO,
        }
        for lcp, lcode in target_rows
    ]

    if quick:
        log(
            f"[점검] 빠른 점검 완료 : ★작업대상 {summary['target_rows']}행 / "
            f"LCP {summary['target_lcps']}종 ({summary['elapsed_sec']}초)"
        )
    else:
        log(
            f"[점검] 완료 : 전체 {summary['total_rows']}행 / "
            f"LCP {summary['total_lcps']}종, "
            f"★작업대상 {summary['target_rows']}행 / "
            f"LCP {summary['target_lcps']}종 ({summary['elapsed_sec']}초)"
        )
    return {"summary": summary, "cells": cells, "items": items}


# ------------------------------------------------------------------ HTTP 점검
#
# 쿠키만 있으면 검색 POST 를 직접 재현할 수 있어 브라우저가 전혀 필요 없다.
# 검색 1회 60초 -> 0.3~2초, 게다가 viewnum 을 크게 줘서 1000행 상한도 없다.

def _quick_http(client, folder_name: str, log=print, progress=None,
                should_stop=None) -> dict:
    """
    빠른 점검 : ★작업대상(이미지승인완료 x 상품정보 미작업) 한 칸만 조회한다.

    주변 합계(대표이미지 승인완료 전체 등)는 채우지 않는다.
    그 값들을 직접 질의하면 응답이 9~12MB라 오히려 12칸 전체점검(약 4초)보다
    느려지기 때문이다. 전체 수량이 필요하면 '전체 점검'을 쓰면 되고,
    그쪽은 12칸 합으로 동일한 값을 정확히 산출한다.
    """
    started = time.time()
    scanned_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    res = client.search(folder_name, C.TARGET_IMAGE_VALUE, C.TARGET_INFO_VALUE)
    rows = res["rows"]
    lcps = {r[0] for r in rows if r[0]}

    log(f"[점검] 대표이미지={C.TARGET_IMAGE} / 상품정보={C.TARGET_INFO} "
        f"→ {len(rows):,}행 / LCP {len(lcps):,}종 ({res['elapsed']}초) ★작업대상")
    if progress:
        progress(1, 1)

    cells = [{"image_status": C.TARGET_IMAGE, "info_status": C.TARGET_INFO,
              "row_count": len(rows), "lcp_count": len(lcps),
              "capped": 0, "is_target": 1}]

    summary = {
        "folder_name": folder_name,
        "scanned_at": scanned_at,
        "mode": "quick",
        # 아래 합계들은 빠른 점검에서 측정하지 않는다 (UI 에서 '-' 로 표시)
        "total_rows": 0, "total_lcps": 0,
        "img_todo_rows": 0, "img_work_rows": 0, "img_done_rows": 0,
        "info_todo_rows": 0, "info_save_rows": 0,
        "info_exclude_rows": 0, "info_hold_rows": 0,
        "target_rows": len(rows),
        "target_lcps": len(lcps),
        "capped": 0,
        "elapsed_sec": round(time.time() - started, 1),
        "note": "HTTP-quick",
    }
    items = [{"folder_name": folder_name, "bucket": "target",
              "lcp_code": lcp, "l_code": lcode,
              "image_status": C.TARGET_IMAGE, "info_status": C.TARGET_INFO}
             for lcp, lcode in rows]

    log(f"[점검] 빠른 점검 완료 ({summary['elapsed_sec']}초) : "
        f"★작업대상 {summary['target_rows']:,}행 / LCP {summary['target_lcps']:,}종")
    return {"summary": summary, "cells": cells, "items": items}


def inspect_folder_http(client, folder_name: str, log=print, progress=None,
                        should_stop=None, quick: bool = False) -> dict:
    """
    HTTP 로 상태별 수량 점검. inspect_folder() 와 동일한 형태를 반환한다.
    """
    if quick:
        return _quick_http(client, folder_name, log=log, progress=progress,
                           should_stop=should_stop)

    started = time.time()
    scanned_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    img_filters = list(C.IMAGE_FILTERS)
    info_filters = list(C.INFO_FILTERS)

    cells, target_rows = [], []
    all_rows = set()
    total_cells = len(img_filters) * len(info_filters)
    done = 0

    for img_label, img_val in img_filters:
        for info_label, info_val in info_filters:
            if should_stop and should_stop():
                log("[점검] 사용자 중단")
                break

            res = client.search(folder_name, img_val, info_val)
            rows = res["rows"]
            lcps = {r[0] for r in rows if r[0]}
            is_target = (img_label == C.TARGET_IMAGE and info_label == C.TARGET_INFO)

            cells.append({
                "image_status": img_label,
                "info_status": info_label,
                "row_count": len(rows),
                "lcp_count": len(lcps),
                "capped": 0,          # viewnum 을 크게 주므로 상한 없음
                "is_target": int(is_target),
            })
            all_rows.update(rows)
            if is_target:
                target_rows = rows

            mark = " ★작업대상" if is_target else ""
            log(f"[점검] 대표이미지={img_label} / 상품정보={info_label} "
                f"→ {len(rows):,}행 / LCP {len(lcps):,}종 "
                f"({res['elapsed']}초){mark}")

            done += 1
            if progress:
                progress(done, total_cells)

    target_lcps = {r[0] for r in target_rows if r[0]}
    all_lcps = {r[0] for r in all_rows if r[0]}

    def total(dim, label, key):
        return sum(c[key] for c in cells if c[dim] == label)

    summary = {
        "folder_name": folder_name,
        "scanned_at": scanned_at,
        "mode": "quick" if quick else "full",
        "total_rows": 0 if quick else len(all_rows),
        "total_lcps": 0 if quick else len(all_lcps),
        "img_todo_rows": total("image_status", "미작업", "row_count"),
        "img_work_rows": total("image_status", "이미지작업", "row_count"),
        "img_done_rows": total("image_status", "이미지승인완료", "row_count"),
        "info_todo_rows": total("info_status", "미작업", "row_count"),
        "info_save_rows": total("info_status", "저장완료", "row_count"),
        "info_exclude_rows": total("info_status", "제외", "row_count"),
        "info_hold_rows": total("info_status", "보류", "row_count"),
        "target_rows": len(target_rows),
        "target_lcps": len(target_lcps),
        "capped": 0,
        "elapsed_sec": round(time.time() - started, 1),
        "note": "HTTP",
    }

    items = [
        {"folder_name": folder_name, "bucket": "target",
         "lcp_code": lcp, "l_code": lcode,
         "image_status": C.TARGET_IMAGE, "info_status": C.TARGET_INFO}
        for lcp, lcode in target_rows
    ]

    log(f"[점검] 완료 ({summary['elapsed_sec']}초) : ★작업대상 "
        f"{summary['target_rows']:,}행 / LCP {summary['target_lcps']:,}종")
    return {"summary": summary, "cells": cells, "items": items}


# ------------------------------------------------------------------ 진단용 덤프

def dump_page_structure(driver, folder_name: str = "", page_size: str = "1000",
                        rows_limit: int = 15, log=print) -> str:
    """
    실제 페이지의 select 목록 / 그리드 헤더 / 앞쪽 행들을 텍스트로 덤프.
    상태 텍스트가 예상과 다를 때 판정 규칙을 보정하기 위한 진단용.
    """
    from ..config import LOG_DIR
    from .browser import visible_selects

    if folder_name:
        open_folder_search(driver, folder_name, page_size, log=log)
    else:
        driver.get(C.SS_IMAGE_URL)
        time.sleep(1)
        accept_all_alerts(driver)
        enter_content_frame(driver)

    lines = [
        "=" * 70,
        f"페이지 구조 덤프 - {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"URL    : {driver.current_url}",
        f"폴더   : {folder_name or '(미선택)'}",
        "=" * 70,
        "",
        "[ SELECT 목록 ]",
    ]

    from selenium.webdriver.support.ui import Select
    for i, el in enumerate(visible_selects(driver)):
        try:
            sel = Select(el)
            opts = sel.options
        except Exception:
            continue
        name = el.get_attribute("name") or el.get_attribute("id") or "?"
        lines.append(f"  select[{i}] name/id={name} (옵션 {len(opts)}개)")
        for o in opts[:8]:
            try:
                lines.append(
                    f"      - TEXT='{(o.text or '').strip()}' "
                    f"VALUE='{(o.get_attribute('value') or '').strip()}'"
                )
            except Exception:
                continue
        if len(opts) > 8:
            lines.append(f"      ... 외 {len(opts) - 8}개")

    colmap = header_map(driver)
    lines += ["", "[ 그리드 헤더 ]", f"  {colmap}", "", "[ 데이터 행 ]"]

    rows = result_rows(driver)
    lines.append(f"  총 {len(rows)}행 (앞 {min(rows_limit, len(rows))}행만 표시)")
    for i, r in enumerate(rows[:rows_limit]):
        try:
            tds = r.find_elements(By.TAG_NAME, "td")
        except Exception:
            continue
        lines.append(f"  --- row {i + 1} ({len(tds)}칸) ---")
        for j, td in enumerate(tds):
            try:
                txt = " ".join((td.text or "").split())
                kind, shown = cell_color_kind(td)
            except Exception:
                txt, kind, shown = "", "?", "?"
            lines.append(f"      td[{j}] '{txt[:60]}'  색={kind} ({shown[:60]})")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"page_dump_{datetime.now():%Y%m%d_%H%M%S}.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    log(f"[덤프] 저장 완료 : {path}")
    return str(path)
