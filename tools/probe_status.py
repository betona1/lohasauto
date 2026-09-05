"""
1) 상태 셀의 CSS 클래스 분포를 수집
2) 필터 콤보(dest_list/attribute/dest_attr/dest_detail)를 하나씩 걸고
   어느 컬럼의 클래스가 바뀌는지 관찰 -> select<->컬럼 매핑 확정
"""
import io, sys, time
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from app.lohas.browser import open_logged_in_browser, accept_all_alerts
from app.lohas.ss_image import open_folder_search, result_rows, click_search

def p(m): print(m, flush=True)

FOLDER = "594. 광고진행-비트마인드"
COLS = {5: "대표이미지", 6: "상품정보", 7: "상세이미지"}


def cell_classes(d):
    """행별 td[5],td[6],td[7] 안 버튼의 class 를 한 번에 뽑는다 (JS로 빠르게)."""
    return d.execute_script("""
        const out = [];
        const trs = document.querySelectorAll('table.grid_tbl tbody tr');
        for (const tr of trs) {
          const tds = tr.querySelectorAll('td');
          if (tds.length < 8) continue;
          const btn = i => {
            const b = tds[i] ? tds[i].querySelector('input,button,a') : null;
            return b ? (b.className || '').trim() : '(none)';
          };
          if (!tds[5] || !tds[5].querySelector('input')) continue;
          out.push({c5: btn(5), c6: btn(6), c7: btn(7)});
        }
        return out;
    """)


def set_select(d, name, value):
    try:
        el = d.find_element(By.NAME, name)
        Select(el).select_by_value(value)
        return True
    except Exception as e:
        p(f"    !! {name}={value} 설정 실패: {e}")
        return False


def snapshot(d, tag):
    rows = cell_classes(d)
    p(f"  [{tag}] 행수={len(rows)}")
    for i, label in COLS.items():
        key = f"c{i}"
        cnt = Counter(r[key] for r in rows)
        p(f"      {label:<8} {dict(cnt)}")
    return rows


d = open_logged_in_browser(headless=False, log=p)
try:
    p("=" * 70)
    p("[1] 필터 없이 전체 (1000개씩)")
    open_folder_search(d, FOLDER, "1000", log=lambda *_: None)
    snapshot(d, "전체")

    tests = [
        ("dest_list",   "allow", "이미지승인완료"),
        ("dest_list",   "none",  "미작업"),
        ("dest_list",   "done",  "이미지작업"),
        ("attribute",   "none",  "미작업"),
        ("attribute",   "save",  "저장완료"),
        ("dest_attr",   "none",  "미작업"),
        ("dest_attr",   "save",  "저장완료"),
        ("dest_detail", "none",  "미작업"),
        ("dest_detail", "done",  "작업완료"),
    ]

    for name, val, label in tests:
        p("=" * 70)
        p(f"[2] {name} = {val} ({label})")
        open_folder_search(d, FOLDER, "1000", log=lambda *_: None)
        if not set_select(d, name, val):
            continue
        click_search(d, None, log=lambda *_: None)
        time.sleep(1.5)
        accept_all_alerts(d)
        snapshot(d, f"{name}={val}")
finally:
    d.quit()
