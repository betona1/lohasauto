"""쿠키 + requests 로 검색 POST 를 재현하고, viewnum 상한 돌파 여부를 확인."""
import io, sys, json, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import requests
from bs4 import BeautifulSoup
from app.lohas.browser import cookie_path
from app.lohas import constants as C

def p(m): print(m, flush=True)

URL = "http://com.exponet.co.kr/manager/commercial/commercial_ss_image/p/1"
FOLDER = "594. 광고진행-비트마인드"

cookies = {c["name"]: c["value"] for c in json.loads(
    cookie_path().read_text(encoding="utf-8"))}
p(f"쿠키 {len(cookies)}개: {list(cookies)}")

sess = requests.Session()
sess.cookies.update(cookies)
sess.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": URL,
})

def payload(dest_list="all", dest_attr="all", viewnum="1000", order="asc"):
    return {
        "action_mode": "search", "categoryname_select": "master",
        "site_categoryname_search": FOLDER, "site_categoryname_search2": "",
        "dest_list": dest_list, "dest_attr": dest_attr,
        "attribute": "all", "dest_detail": "all", "dest_cate": "all",
        "fc": "product_code", "order": order, "fv": "",
        "viewnum": viewnum,
        "categoryname_select_ai": "master",
        "site_categoryname_search_ai": FOLDER, "site_categoryname_search2_ai": "",
    }

def fetch(**kw):
    t0 = time.time()
    r = sess.post(URL, data=payload(**kw), timeout=120)
    r.encoding = r.apparent_encoding or "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    rows = []
    for tr in soup.select("table.grid_tbl tr"):
        tds = tr.find_all("td")
        if len(tds) < 8:
            continue
        if not tds[5].find("input"):
            continue
        rows.append((tds[1].get_text(strip=True), tds[2].get_text(strip=True)))
    return rows, round(time.time() - t0, 2), len(r.text), r.status_code

p("\n[0] 로그인 상태 확인")
rows, sec, size, code = fetch()
p(f"   HTTP {code} / {size:,}바이트 / {sec}초 / 행 {len(rows)}")
if "loginForm" in sess.get(C.MANAGER_URL, timeout=30).text:
    p("   !! 세션 만료 - 쿠키 재발급 필요"); sys.exit(1)
p("   세션 유효")

tests = [
    ("작업대상 (승인완료+미작업) viewnum=1000",  dict(dest_list="allow", dest_attr="none", viewnum="1000")),
    ("작업대상  viewnum=100000",                dict(dest_list="allow", dest_attr="none", viewnum="100000")),
    ("상한칸 (승인완료+저장완료) viewnum=1000",  dict(dest_list="allow", dest_attr="save", viewnum="1000")),
    ("상한칸  viewnum=5000",                    dict(dest_list="allow", dest_attr="save", viewnum="5000")),
    ("상한칸  viewnum=100000",                  dict(dest_list="allow", dest_attr="save", viewnum="100000")),
    ("전체 (필터없음) viewnum=100000",           dict(viewnum="100000")),
]
p("")
for label, kw in tests:
    rows, sec, size, code = fetch(**kw)
    p(f"[{label}]")
    p(f"    행 {len(rows):,} / 고유LCP {len(set(r[0] for r in rows)):,} "
      f"| {sec}초 | {size:,}바이트 | HTTP {code}")
