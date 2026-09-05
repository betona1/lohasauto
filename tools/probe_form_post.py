"""listForm 의 action/method/필드를 덤프하고, requests 로 재현 가능한지 검증."""
import io, sys, json, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app.lohas.browser import open_logged_in_browser, save_cookies, cookie_path
from app.lohas.ss_image import open_folder_search

def p(m): print(m, flush=True)
FOLDER = "594. 광고진행-비트마인드"

FORM_JS = """
const f = document.forms['listForm'] || document.querySelector('form');
if(!f) return JSON.stringify({error:'listForm 없음'});
const fields=[];
for (const el of f.elements) {
  if(!el.name) continue;
  let v = el.value;
  if (el.tagName==='SELECT') v = el.options[el.selectedIndex] ? el.options[el.selectedIndex].value : '';
  if ((el.type==='checkbox'||el.type==='radio') && !el.checked) continue;
  fields.push({name:el.name, tag:el.tagName, type:el.type||'', value:v});
}
return JSON.stringify({
  action: f.getAttribute('action')||'', method:(f.getAttribute('method')||'get'),
  url: location.href, fields: fields
});
"""

d = open_logged_in_browser(headless=False, log=p)
try:
    open_folder_search(d, FOLDER, "1000", log=lambda *a, **k: None)
    save_cookies(d, log=p)
    info = json.loads(d.execute_script(FORM_JS))
    p("=" * 70)
    p(f"action  : {info.get('action')!r}")
    p(f"method  : {info.get('method')!r}")
    p(f"현재URL : {info.get('url')}")
    p(f"필드 {len(info.get('fields', []))}개")
    for f_ in info.get("fields", []):
        p(f"   {f_['name']:30} {f_['tag']:8} {f_['type']:10} = {f_['value']!r}")
    Path("logs/listform.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    p("\nlogs/listform.json 저장")
    p(f"쿠키파일 : {cookie_path()}")
finally:
    try: d.quit()
    except Exception: pass
