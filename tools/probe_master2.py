"""2차 마스터 콤보(site_categoryname_search2)가 실제 필터로 동작하는지 확인."""
import io, sys, time, json
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

COUNT_JS = """
let n=0;
for (const tr of document.querySelectorAll('table.grid_tbl tbody tr')) {
  const t=tr.querySelectorAll('td');
  if (t.length>=8 && t[5].querySelector('input')) n++;
}
return n;
"""

# 각 select 가 어느 form 소속인지 / 보이는지 / 동명 요소가 몇 개인지
STRUCT_JS = """
const out=[];
document.querySelectorAll('select').forEach((s,i)=>{
  const f=s.form;
  const same=document.getElementsByName(s.name||'').length;
  out.push({i:i, name:(s.name||s.id||'?'),
            form:(f?(f.getAttribute('name')||f.getAttribute('id')||'(무명)'):'(없음)'),
            action:(f?(f.getAttribute('action')||''):''),
            shown:!!(s.offsetParent), disabled:s.disabled,
            sameName:same, sel:(s.selectedIndex>=0?s.options[s.selectedIndex].value:'')});
});
return JSON.stringify(out);
"""

d = open_logged_in_browser(headless=False, log=p)
try:
    open_folder_search(d, FOLDER, "1000", log=quiet)

    p("=" * 72)
    p("[A] select 구조 (form 소속 / 표시 / 동명요소 개수)")
    for s_ in json.loads(d.execute_script(STRUCT_JS)):
        p(f"  [{s_['i']:2}] {s_['name']:28} form={s_['form']:<14} "
          f"보임={str(s_['shown']):5} disabled={str(s_['disabled']):5} "
          f"동명={s_['sameName']} 현재값={s_['sel']!r}")

    p("\n[B] 검색 버튼이 제출하는 form 확인")
    p("  " + str(d.execute_script("""
        const btns=[...document.querySelectorAll("input[type='button'],input[type='submit']")]
          .filter(b=>((b.value||'').indexOf('검색')>=0));
        return btns.map(b=>{const f=b.form;
          return (b.value||'')+' -> form='+(f?(f.getAttribute('name')||f.getAttribute('id')||'(무명)'):'(없음)');
        }).join(' | ');
    """)))

    p("\n[C] 2차 마스터 옵션")
    sel2 = Select(d.find_element(By.NAME, "site_categoryname_search2"))
    opts = [(o.get_attribute("value"), o.text.strip()) for o in sel2.options]
    for v, t in opts:
        p(f"    value={v!r:34} text={t!r}")

    def run(master2=None, master1=FOLDER, il="allow", ia="save"):
        open_folder_search(d, master1, "1000", log=quiet) if master1 else None
        if not master1:
            from app.lohas.ss_image import open_folder_search as _o
            _o(d, FOLDER, "1000", log=quiet)
        if il:
            Select(d.find_element(By.NAME, "dest_list")).select_by_value(il)
        if ia:
            Select(d.find_element(By.NAME, "dest_attr")).select_by_value(ia)
        if master2 is not None:
            Select(d.find_element(By.NAME, "site_categoryname_search2")).select_by_value(master2)
        click_search(d, None, log=quiet)
        time.sleep(1.4); accept_all_alerts(d)
        return d.execute_script(COUNT_JS)

    p("\n[D] 필터 동작 검증 (기준: 마스터=594 + 이미지승인완료 + 저장완료)")
    p(f"    2차=전체(미설정)            -> {run(None)}행")
    for v, t in opts:
        if not v:
            continue
        p(f"    2차={t[:26]:28} -> {run(v)}행")
finally:
    try: d.quit()
    except Exception: pass
