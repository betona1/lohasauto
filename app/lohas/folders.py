"""
마스터(작업폴더) 목록 스캔.

상품정보관리(commercial_ss_image) 페이지의 '마스터' 콤보에서
'594. 광고진행-비트마인드(764)' 형태의 옵션을 읽어 이름/수량으로 분해한다.

페이지마다 select 순서가 달라서 XPath 를 고정하지 않고,
옵션 내용을 보고 '폴더 콤보' 를 점수로 골라낸다. (LOHASPIC 과 동일한 전략)
"""
import re
import time

from selenium.webdriver.support.ui import Select

from . import constants as C
from .browser import accept_all_alerts, enter_content_frame, visible_selects

# '594. 광고진행-비트마인드(764)' / '594. 광고진행-비트마인드' 둘 다 허용
FOLDER_LABEL_RE = re.compile(r"^\s*\d+\.\s*\S")
COUNT_RE = re.compile(r"\((\d[\d,]*)\)\s*$")

# 폴더 콤보가 아님이 확실한 옵션들 (검색항목/개수보기 콤보)
NON_FOLDER_HINTS = (
    "전체", "선택", "로하스상품코드", "광고상품코드", "상품명",
    "개씩", "개 보기", "정렬",
)


def parse_folder_label(label: str) -> dict:
    """'594. 광고진행-비트마인드(764)' -> {name, site_count, raw_label}"""
    raw = " ".join((label or "").split())
    m = COUNT_RE.search(raw)
    if m:
        name = raw[: m.start()].strip()
        try:
            count = int(m.group(1).replace(",", ""))
        except ValueError:
            count = None
    else:
        name = raw
        count = None
    return {"name": name, "site_count": count, "raw_label": raw}


def _score_select(options: list) -> int:
    """이 select 가 '마스터(작업폴더)' 콤보일 가능성 점수."""
    if len(options) < 3:
        return -1

    texts = [" ".join((o.get("text") or "").split()) for o in options]
    joined = " ".join(texts)

    # 숫자만 있는 콤보(= 개수보기)는 제외
    if all(re.fullmatch(r"\d+\s*(개|건)?", t or "") for t in texts if t):
        return -1
    # 검색항목 콤보 제외
    if any(h in joined for h in ("로하스상품코드", "광고상품코드")) and len(options) < 12:
        return -1

    score = 0
    for t in texts:
        if not t or t in ("전체", "선택"):
            continue
        if FOLDER_LABEL_RE.match(t):
            score += 3
        if COUNT_RE.search(t):
            score += 2
        if "광고진행" in t:
            score += 2
    return score


def _read_options(select_el) -> list:
    out = []
    try:
        sel = Select(select_el)
        for o in sel.options:
            try:
                out.append({
                    "text": (o.text or "").strip(),
                    "value": (o.get_attribute("value") or "").strip(),
                })
            except Exception:
                continue
    except Exception:
        return []
    return out


def scan_master_folders(driver, log=print) -> list:
    """
    ss_image 페이지로 이동해 마스터 폴더 목록을 읽어온다.
    반환: [{name, raw_label, option_value, site_count}, ...]
    """
    driver.get(C.SS_IMAGE_URL)
    time.sleep(1)
    accept_all_alerts(driver)
    enter_content_frame(driver)

    best_options, best_score = None, 0
    for el in visible_selects(driver):
        options = _read_options(el)
        score = _score_select(options)
        if score > best_score:
            best_score, best_options = score, options

    if not best_options:
        raise RuntimeError(
            "마스터(작업폴더) 콤보를 찾지 못했습니다.\n"
            "· 로그인이 정상인지, 상품정보관리 페이지가 열렸는지 확인해주세요.\n"
            "· [페이지 구조 덤프] 버튼으로 실제 select 목록을 확인할 수 있습니다."
        )

    folders, seen = [], set()
    for o in best_options:
        text = " ".join((o["text"] or "").split())
        if not text or text in ("전체", "선택", "-"):
            continue
        if any(h == text for h in NON_FOLDER_HINTS):
            continue

        parsed = parse_folder_label(text)
        if not parsed["name"] or parsed["name"] in seen:
            continue
        seen.add(parsed["name"])
        parsed["option_value"] = o["value"]
        folders.append(parsed)

    log(f"[폴더스캔] {len(folders)}개 폴더 수집 (콤보 점수 {best_score})")
    return folders


def select_folder(driver, folder_name: str, log=print) -> bool:
    """
    페이지의 select 들을 훑어서 작업폴더를 선택.
    LOHASPIC `_select_folder_anywhere` 와 동일한 매칭 전략 (정확 → 부분).
    """
    value_key = folder_name.split("(")[0].strip()
    partial_hit = None

    for el in visible_selects(driver):
        try:
            options = Select(el).options
        except Exception:
            continue

        for opt in options:
            try:
                text = (opt.text or "").strip()
                value = (opt.get_attribute("value") or "").strip()
            except Exception:
                continue

            # 1순위: 정확히 일치
            if folder_name in (text, value) or (value_key and value_key in (text, value)):
                opt.click()
                log(f"[폴더선택] 정확 일치 => {text or value}")
                return True

            # 2순위: 부분 일치 ('594. 광고진행-비트마인드' vs '...(764)')
            if partial_hit is None and len(value_key) >= 2 and value_key in text:
                partial_hit = opt

    if partial_hit is not None:
        label = (partial_hit.text or "").strip()
        partial_hit.click()
        log(f"[폴더선택] 부분 일치 => {label}")
        return True

    return False


def select_view_count(driver, count: str = "1000", log=print) -> bool:
    """'한 페이지에 N개씩 보기' 콤보 지정. 없으면 가장 큰 값."""
    best_opt, best_val = None, -1

    for el in visible_selects(driver):
        try:
            options = Select(el).options
        except Exception:
            continue

        nums = []
        for opt in options:
            try:
                v = (opt.get_attribute("value") or "").strip()
                t = (opt.text or "").strip()
            except Exception:
                nums = []
                break
            m = re.fullmatch(r"(\d+)\s*(개|건)?", t) or re.fullmatch(r"\d+", v)
            if not m:
                nums = []
                break
            try:
                nums.append((int(v or re.findall(r"\d+", t)[0]), opt))
            except Exception:
                nums = []
                break

        if not nums:
            continue

        for n, opt in nums:
            if str(n) == str(count):
                opt.click()
                log(f"[조회설정] 한 페이지 {count}개씩 보기")
                return True
            if n > best_val:
                best_val, best_opt = n, opt

    if best_opt is not None:
        best_opt.click()
        log(f"[조회설정] '{count}개' 없음 → 최대 {best_val}개씩 보기")
        return True

    log("[조회설정] 개수보기 콤보를 찾지 못함 (기본값 사용)")
    return False
