"""필터 콤보를 걸어 class <-> 상태, select <-> 컬럼 매핑을 확정 (viewnum=20, 가볍게)."""
import io, sys, time
from collections import Counter
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

JS = """
const out=[];
for (const tr of document.querySelectorAll('table.grid_tbl tbody tr')) {
  const tds=tr.querySelectorAll('td');
  if (tds.length<8 || !tds[5].querySelector('input')) continue;
  const g=i=>{const b=tds[i].querySelector('input,button,a');return b?(b.className||'').trim():'(none)';};
  out.push({c5:g(5),c6:g(6),c7:g(7)});
}
return out;
"""

d = open_logged_in_browser(headless=False, log=p)
try:
    tests = [
        ("(필터없음)", None, None),
        ("dest_list",   "allow", "이미지승인완료"),
        ("dest_list",   "none",  "미작업"),
        ("attribute",   "none",  "미작업"),
        ("attribute",   "save",  "저장완료"),
        ("dest_detail", "none",  "미작업"),
    ]
    for name, val, label in tests:
        open_folder_search(d, FOLDER, "20", log=quiet)
        tag = "필터없음"
        if name != "(필터없음)":
            try:
                Select(d.find_element(By.NAME, name)).select_by_value(val)
            except Exception as e:
                p(f"[{name}={val}] 설정실패 {e}"); continue
            click_search(d, None, log=quiet)
            time.sleep(1.2); accept_all_alerts(d)
            tag = f"{name}={val} ({label})"
        rows = d.execute_script(JS)
        p("-" * 66)
        p(f"[{tag}]  행수={len(rows)}")
        for i, lab in ((5, "대표이미지"), (6, "상품정보"), (7, "상세이미지")):
            p(f"    {lab:<8} {dict(Counter(r[f'c{i}'] for r in rows))}")
finally:
    d.quit()
