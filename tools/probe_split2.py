"""상한 초과 칸을 쪼갤 축 탐색 (경량판: 매 검색마다 결과수만 읽음)."""
import io, sys, time, json
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
  const t=tr.querySelectorAll('td');
  if (t.length>=8 && t[5].querySelector('input')) n++;
}
return n;
"""

def prep(d):
    open_folder_search(d, FOLDER, "1000", log=quiet)
    Select(d.find_element(By.NAME, "dest_list")).select_by_value("allow")
    Select(d.find_element(By.NAME, "dest_attr")).select_by_value("save")

def go(d):
    click_search(d, None, log=quiet)
    time.sleep(1.3); accept_all_alerts(d)
    return d.execute_script(COUNT_JS)

d = open_logged_in_browser(headless=False, log=p)
try:
    prep(d)
    st = json.loads(d.execute_script(
        "const o={};for(const s of document.querySelectorAll('select')){"
        "o[s.name||s.id]={d:s.disabled,n:s.options.length};}return JSON.stringify(o);"))
    p("[0] 필터 적용 상태의 select disabled 여부")
    for k, v in st.items():
        p(f"    {k:28} disabled={v['d']} 옵션={v['n']}")
    p(f"\n[1] 기준(이미지승인완료 x 저장완료) = {go(d)}행 (1000 상한)")

    p("\n[2] dest_detail (상세이미지) 분할")
    for val, lab in (("none", "미작업"), ("done", "작업완료")):
        prep(d)
        try:
            Select(d.find_element(By.NAME, "dest_detail")).select_by_value(val)
        except Exception as e:
            p(f"    {lab:8} -> 사용불가 ({str(e)[:45]})"); continue
        p(f"    {lab:8} -> {go(d)}행")

    p("\n[3] fc + 검색어 부분일치 (분할 가능성)")
    for fcv, fclab, kws in (("lcode", "로하스상품코드", ("L1", "L2", "L3")),):
        for kw in kws:
            prep(d)
            try:
                Select(d.find_element(By.NAME, "fc")).select_by_value(fcv)
            except Exception as e:
                p(f"    fc 실패 {str(e)[:45]}"); break
            el = find_search_input(d)
            if el is None:
                p("    검색어칸 없음"); break
            try:
                el.clear(); el.send_keys(kw)
            except Exception:
                pass
            p(f"    {fclab} '{kw}' -> {go(d)}행")
finally:
    try: d.quit()
    except Exception: pass
