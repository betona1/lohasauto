"""목표 조합(대표이미지 승인완료 + 상품정보 미작업) 실측 + 페이징 총건수 확인."""
import io, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from app.lohas.browser import open_logged_in_browser, accept_all_alerts
from app.lohas.ss_image import open_folder_search, click_search

def p(m): print(m, flush=True)
FOLDER = "594. 광고진행-비트마인드"
quiet = lambda *a, **k: None

ROWS_JS = """
const out=[];
for (const tr of document.querySelectorAll('table.grid_tbl tbody tr')) {
  const tds=tr.querySelectorAll('td');
  if (tds.length<8 || !tds[5].querySelector('input')) continue;
  out.push(tds[1].innerText.trim());
}
return out;
"""

PAGE_JS = """
const as=[...document.querySelectorAll('a')];
const info=as.map(a=>({t:(a.innerText||'').trim(),
                       h:(a.getAttribute('href')||'').slice(0,60),
                       o:(a.getAttribute('onclick')||'').slice(0,60)}))
             .filter(x=>x.t.length>0 && x.t.length<8);
return JSON.stringify(info.slice(0,30));
"""

COMBOS = [
    ("목표: 이미지승인완료 + 상품정보미작업", {"dest_list": "allow", "dest_attr": "none"}),
    ("이미지승인완료 + 상품정보저장완료",     {"dest_list": "allow", "dest_attr": "save"}),
    ("이미지승인완료 + 상품정보보류",         {"dest_list": "allow", "dest_attr": "hold"}),
    ("이미지작업(승인전) 전체",              {"dest_list": "done"}),
]

d = open_logged_in_browser(headless=False, log=p)
try:
    for label, filters in COMBOS:
        open_folder_search(d, FOLDER, "1000", log=quiet)
        ok = True
        for name, val in filters.items():
            try:
                Select(d.find_element(By.NAME, name)).select_by_value(val)
            except Exception as e:
                p(f"[{label}] {name}={val} 실패: {str(e)[:60]}"); ok = False; break
        if not ok:
            continue
        click_search(d, None, log=quiet)
        time.sleep(1.5); accept_all_alerts(d)
        rows = d.execute_script(ROWS_JS)
        p("-" * 66)
        p(f"[{label}]")
        p(f"    행(L코드) 수 = {len(rows)}   고유 LCP = {len(set(rows))}")
        if len(rows) >= 1000:
            p("    ※ 1000행 꽉 참 -> 다음 페이지 존재")
        p(f"    페이징링크 = {d.execute_script(PAGE_JS)}")
finally:
    d.quit()
