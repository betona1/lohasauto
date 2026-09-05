"""검색 1회로 상태 셀 CSS 클래스 분포를 수집 (가볍게)."""
import io, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app.lohas.browser import open_logged_in_browser
from app.lohas.ss_image import open_folder_search

def p(m): print(m, flush=True)

FOLDER = "594. 광고진행-비트마인드"
VIEW = sys.argv[1] if len(sys.argv) > 1 else "1000"

JS = """
const out = [];
for (const tr of document.querySelectorAll('table.grid_tbl tbody tr')) {
  const tds = tr.querySelectorAll('td');
  if (tds.length < 8) continue;
  if (!tds[5].querySelector('input')) continue;
  const g = i => {
    const b = tds[i].querySelector('input,button,a');
    if (!b) return '(none)';
    return (b.className||'').trim() + '|' + (b.value||b.textContent||'').trim();
  };
  out.push({lcp: tds[1].innerText.trim(), c5: g(5), c6: g(6), c7: g(7)});
}
return out;
"""

d = open_logged_in_browser(headless=False, log=p)
try:
    open_folder_search(d, FOLDER, VIEW, log=p)
    rows = d.execute_script(JS)
    p("=" * 70)
    p(f"수집 행수 : {len(rows)}")
    p(f"고유 LCP  : {len(set(r['lcp'] for r in rows))}")
    for i, label in ((5, "대표이미지"), (6, "상품정보"), (7, "상세이미지")):
        cnt = Counter(r[f"c{i}"] for r in rows)
        p(f"\n[{label}] 클래스|값 분포")
        for k, v in cnt.most_common():
            p(f"    {v:>6}건  {k}")
finally:
    d.quit()
