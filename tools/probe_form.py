"""검색폼의 select <-> 라벨 매핑, 총건수 표시, 상태셀 내부 HTML 을 확인."""
import io, sys, time, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from selenium.webdriver.common.by import By
from app import config
from app.lohas import constants as C
from app.lohas.browser import open_logged_in_browser, accept_all_alerts, enter_content_frame
from app.lohas.ss_image import open_folder_search, result_rows, header_map

def p(m): print(m, flush=True)

FOLDER = "594. 광고진행-비트마인드"
d = open_logged_in_browser(headless=False, log=p)
try:
    open_folder_search(d, FOLDER, "20", log=p)

    p("=" * 70)
    p("[A] select <-> 라벨 매핑")
    for el in d.find_elements(By.TAG_NAME, "select"):
        if not el.is_displayed():
            continue
        name = el.get_attribute("name") or el.get_attribute("id") or "?"
        # 부모 td 의 앞 형제 td 텍스트 = 대개 라벨
        label = d.execute_script("""
            const el = arguments[0];
            let td = el.closest('td') || el.parentElement;
            let prev = td ? td.previousElementSibling : null;
            let own  = td ? td.innerText : '';
            let tr   = el.closest('tr');
            return JSON.stringify({
              prevTd: prev ? prev.innerText.trim() : '',
              ownTd : (own||'').trim().slice(0,80),
              rowTxt: tr ? tr.innerText.replace(/\s+/g,' ').trim().slice(0,160) : ''
            });
        """, el)
        p(f"  · {name}")
        p(f"      {label}")

    p("=" * 70)
    p("[B] 총 건수 표시 후보")
    body = d.find_element(By.TAG_NAME, "body").text
    for line in body.splitlines():
        t = line.strip()
        if re.search(r"(총|전체|건|개)\s*[:：]?\s*[\d,]+", t) and len(t) < 120:
            p(f"  · {t}")

    p("=" * 70)
    p("[C] 상태 셀 내부 HTML (앞 3행, 대표이미지/상품정보/상세이미지)")
    colmap = header_map(d)
    p(f"  헤더: {colmap}")
    rows = result_rows(d)
    p(f"  행수: {len(rows)}")
    for i, r in enumerate(rows[:3]):
        tds = r.find_elements(By.TAG_NAME, "td")
        p(f"  --- row {i+1} ---")
        for j in (5, 6, 7):
            if j < len(tds):
                html = tds[j].get_attribute("innerHTML")
                html = re.sub(r"\s+", " ", html or "").strip()
                p(f"      td[{j}] HTML= {html[:400]}")
finally:
    d.quit()
