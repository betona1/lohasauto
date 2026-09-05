"""상한(2000)을 넘는 칸을 더 쪼갤 수 있는 축을 찾는다."""
import io, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from app.lohas.browser import open_logged_in_browser, accept_all_alerts
from app.lohas.ss_image import open_folder_search, click_search, find_search_input

def p(m): print(m, flush=True)
FOLDER = "594. 광고진행-비트마인드"
quiet = lambda *a, **k: None

COUNT_JS = """
let n=0;
for (const tr of document.querySelectorAll('table.grid_tbl tbody tr')) {
  const tds=tr.querySelectorAll('td');
  if (tds.length>=8 && tds[5].querySelector('input')) n++;
}
return n;
"""

STATE_JS = """
const out={};
for (const s of document.querySelectorAll('select')) {
  const n=s.getAttribute('name')||s.getAttribute('id')||'?';
  out[n]={disabled:s.disabled, opts:[...s.options].map(o=>o.value+'|'+o.text.trim())};
}
return JSON.stringify(out);
"""

def prep():
    open_folder_search(d, FOLDER, "1000", log=quiet)
    Select(d.find_element(By.NAME, "dest_list")).select_by_value("allow")
    Select(d.find_element(By.NAME, "dest_attr")).select_by_value("save")

def go():
    click_search(d, None, log=quiet)
    time.sleep(1.4); accept_all_alerts(d)
    return d.execute_script(COUNT_JS)

d = open_logged_in_browser(headless=False, log=p)
try:
    p("=" * 66)
    p("[0] 필터 적용 후 select 들의 disabled 상태")
    prep()
    import json
    st = json.loads(d.execute_script(STATE_JS))
    for k in ("dest_detail", "dest_cate", "fc", "site_categoryname_search2"):
        if k in st:
            p(f"   {k:26} disabled={st[k]['disabled']}  옵션={st[k]['opts'][:6]}")

    base = go()
    p(f"\n[1] 기준 (이미지승인완료 x 저장완료) = {base}행 (1000상한)")

    # --- 축 후보 1: dest_detail (상세이미지) ---
    p("\n[2] dest_detail (상세이미지) 로 분할 시도")
    for val, lab in (("none", "미작업"), ("done", "작업완료")):
        prep()
        try:
            Select(d.find_element(By.NAME, "dest_detail")).select_by_value(val)
        except Exception as e:
            p(f"   {lab:8} -> 사용불가: {str(e)[:60]}"); continue
        p(f"   {lab:8} -> {go()}행")

    # --- 축 후보 2: dest_cate ---
    p("\n[3] dest_cate 로 분할 시도")
    for val, lab in (("del", "재작업대상"), ("capacity", "작업완료-총용량확인")):
        prep()
        try:
            Select(d.find_element(By.NAME, "dest_cate")).select_by_value(val)
        except Exception as e:
            p(f"   {lab:16} -> 사용불가: {str(e)[:60]}"); continue
        p(f"   {lab:16} -> {go()}행")

    # --- 축 후보 3: 2차 마스터 ---
    p("\n[4] site_categoryname_search2 (2차 마스터) 로 분할 시도")
    prep()
    try:
        sel2 = Select(d.find_element(By.NAME, "site_categoryname_search2"))
        opts = [(o.get_attribute("value"), o.text.strip()) for o in sel2.options]
    except Exception as e:
        opts = []; p(f"   사용불가: {str(e)[:60]}")
    for val, lab in opts[:9]:
        if not val:
            continue
        prep()
        try:
            Select(d.find_element(By.NAME, "site_categoryname_search2")).select_by_value(val)
        except Exception:
            continue
        p(f"   {lab[:28]:30} -> {go()}행")

    # --- 축 후보 4: fc + 검색어 부분일치 ---
    p("\n[5] fc=로하스상품코드 + 검색어 부분일치 가능한지")
    for kw in ("L1", "L2"):
        prep()
        try:
            Select(d.find_element(By.NAME, "fc")).select_by_value("lcode")
        except Exception as e:
            p(f"   fc 설정 실패: {str(e)[:60]}"); break
        el = find_search_input(d)
        if el is None:
            p("   검색어칸 없음"); break
        el.clear(); el.send_keys(kw)
        p(f"   검색어 '{kw}' -> {go()}행")
finally:
    d.quit()
