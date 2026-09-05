"""1000건 초과 시 /p/N URL 로 페이징이 되는지, 필터가 유지되는지 확인."""
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
BASE = "http://com.exponet.co.kr/manager/commercial/commercial_ss_image/p/"
quiet = lambda *a, **k: None

ROWS_JS = """
const out=[];
for (const tr of document.querySelectorAll('table.grid_tbl tbody tr')) {
  const tds=tr.querySelectorAll('td');
  if (tds.length<8 || !tds[5].querySelector('input')) continue;
  const b=tds[6].querySelector('input,button,a');
  out.push(tds[1].innerText.trim()+'/'+tds[2].innerText.trim()+'/'+(b?b.className.trim():''));
}
return out;
"""

d = open_logged_in_browser(headless=False, log=p)
try:
    # 1000건이 넘는 조합: 이미지승인완료 + 상품정보 저장완료
    open_folder_search(d, FOLDER, "1000", log=quiet)
    Select(d.find_element(By.NAME, "dest_list")).select_by_value("allow")
    Select(d.find_element(By.NAME, "dest_attr")).select_by_value("save")
    click_search(d, None, log=quiet)
    time.sleep(1.5); accept_all_alerts(d)

    r1 = d.execute_script(ROWS_JS)
    p(f"[p/1] 행={len(r1)} 고유={len(set(r1))} 첫={r1[0][:40] if r1 else '-'}")

    for page in (2, 3):
        d.get(BASE + str(page))
        time.sleep(1.8); accept_all_alerts(d)
        rn = d.execute_script(ROWS_JS)
        overlap = len(set(rn) & set(r1))
        info_cls = set(x.split('/')[-1] for x in rn)
        p(f"[p/{page}] 행={len(rn)} 고유={len(set(rn))} p1과겹침={overlap} "
          f"상품정보클래스={info_cls} 첫={rn[0][:40] if rn else '-'}")
        # 필터 유지 확인: 저장완료면 전부 btn_mb 여야 함
        if rn and info_cls == {"btn_m btn_mb"}:
            p("      -> 필터 유지됨 (전부 저장완료)")
        elif rn:
            p("      -> ※ 필터가 풀렸을 가능성")
finally:
    d.quit()
