"""
네이버 데이터랩 카테고리 / 인기키워드 — 100번 서버 naverterms 백엔드 경유.

100번 서버(사내 100번 서버)에 이미 구현돼 있는 API 를 그대로 쓴다.
내부망이라 인증 없이 호출된다.

  GET  /api/naver/datalab/categories/?cid={상위코드}     하위 카테고리 (루트는 cid=0)
  GET  /api/naver/datalab/category-keywords/            카테고리별 인기키워드 (최대 500)
         ?cid&startDate&endDate&age&gender&device
  POST /api/naver/datalab/enrich-keywords/             검색량·상품수 보강
  POST /api/naver/datalab/auto-match/                  상품명↔키워드 자동매칭
  GET  /api/naver/datalab/category-names/?cids=a,b,c
  GET  /api/naver/buy-keywords/{productCode}/          상품코드별 구매키워드
  GET  /api/naver/related-keywords/?keyword=

카테고리 코드(cid)는 네이버 쇼핑 카테고리 체계이고, 로하스 `prod_category` 가
돌려주는 code 와 같은 체계로 보인다(50004771 등). 매핑에 쓸 수 있다.
"""
import datetime
import json
import re
import time
import urllib.parse
import urllib.request

from .. import config

# 주소는 config 가 내부/외부 프로파일에 맞춰 준다. 외부망에서 비어 있으면
# 데이터랩 기능만 꺼지고 나머지는 그대로 돈다.
TIMEOUT = 60

stats = {"call": 0, "ok": 0, "fail": 0}


def base() -> str:
    return config.datalab_base()


def _get(path: str, params: dict = None, timeout: int = TIMEOUT):
    root = base()
    if not root:
        raise RuntimeError(
            "데이터랩 서버 주소가 없습니다. 외부망이라면 .env 의 "
            "DATALAB_HOST_EXTERNAL 을 설정하세요.")
    url = root + path
    if params:
        url += "?" + urllib.parse.urlencode(
            {k: v for k, v in params.items() if v not in (None, "")})
    stats["call"] += 1
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        stats["ok"] += 1
        return data
    except Exception:
        stats["fail"] += 1
        raise


def ping() -> bool:
    if not base():
        return False
    try:
        _get("/datalab/categories/", {"cid": "0"}, timeout=10)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ 카테고리

def children(cid: str = "0") -> list:
    """하위 카테고리 목록. 루트는 cid='0'."""
    data = _get("/datalab/categories/", {"cid": cid})
    return data if isinstance(data, list) else (data.get("categories") or [])


def fetch_tree(max_depth: int = 4, delay: float = 0.05, log=print) -> list:
    """
    카테고리 트리 전체를 평평한 목록으로 수집.

    반환: [{cid, name, pid, depth, path}] — path 는 '대 > 중 > 소' 형태
    """
    out, seen = [], set()

    def walk(cid, depth, prefix):
        if depth > max_depth:
            return
        try:
            kids = children(cid)
        except Exception as e:
            log(f"    ! cid={cid} 조회 실패: {str(e)[:60]}")
            return
        for k in kids:
            kid = str(k.get("cid") or "").strip()
            name = (k.get("name") or "").strip()
            if not kid or kid == cid or kid in seen:
                continue          # 루트가 그대로 되돌아오는 경우 방지
            seen.add(kid)
            path = f"{prefix} > {name}" if prefix else name
            out.append({"cid": kid, "name": name, "pid": cid,
                        "depth": depth, "path": path})
            if delay:
                time.sleep(delay)
            walk(kid, depth + 1, path)

    log("[데이터랩] 카테고리 트리 수집 시작")
    walk("0", 1, "")
    log(f"[데이터랩] 카테고리 {len(out):,}개 수집")
    return out


def tree_path():
    return config.SQLITE_PATH.parent / "naver_categories.json"


def save_tree(rows: list) -> str:
    p = tree_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return str(p)


def load_tree() -> list:
    p = tree_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


# ------------------------------------------------------------------ 인기키워드

def category_keywords(cid: str, days: int = 30, age: str = "",
                      gender: str = "", device: str = "") -> dict:
    """
    카테고리별 인기키워드 (최대 500위).
    반환: {'ranks': [{rank, keyword, linkId}...], 'cached': bool, 'cached_at': ...}
    """
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days)
    return _get("/datalab/category-keywords/", {
        "cid": cid, "startDate": str(start), "endDate": str(end),
        "age": age, "gender": gender, "device": device,
    })


def enrich(keywords: list) -> dict:
    """검색량·상품수 보강 (POST)."""
    body = json.dumps({"keywords": keywords}).encode("utf-8")
    req = urllib.request.Request(
        base() + "/datalab/enrich-keywords/", data=body,
        headers={"Content-Type": "application/json"})
    stats["call"] += 1
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        stats["ok"] += 1
        return data.get("data") or data
    except Exception:
        stats["fail"] += 1
        return {}


def buy_keywords(product_code: str) -> dict:
    """상품코드(LCP)별 구매키워드."""
    return _get(f"/buy-keywords/{urllib.parse.quote(product_code)}/")


def related_keywords(keyword: str) -> dict:
    return _get("/related-keywords/", {"keyword": keyword})


# ------------------------------------------------------------------ 수집 루틴

def collect(db, cids: list, days: int = 30, redo: bool = False, log=print,
            progress=None, should_stop=None, delay: float = 0.2) -> dict:
    """
    카테고리별 인기키워드(최대 500)를 모아 저장한다.

    cids 는 [(cid, 이름)] 또는 [cid]. 이미 수집한 카테고리는 건너뛴다(redo=True 면 다시).
    100번 서버가 캐시를 갖고 있어 두 번째부터는 즉시 돌아온다.
    """
    have = db.datalab_have() if not redo else {}
    items = [(c, "") if isinstance(c, str) else (str(c[0]), c[1] or "")
             for c in cids]
    todo = [(c, n) for c, n in items if c and c not in have]
    log(f"[데이터랩] 카테고리 {len(items):,}개 중 미수집 {len(todo):,}개")

    ok = fail = skip = 0
    words = 0
    for i, (cid, name) in enumerate(todo, 1):
        if should_stop and should_stop():
            log("[데이터랩] 사용자 중단")
            break
        try:
            res = category_keywords(cid, days=days)
            ranks = res.get("ranks") or []
            if not ranks:
                skip += 1
                log(f"  - {cid} {name[:30]} : 키워드 없음")
                continue
            st = db.save_datalab_keywords(cid, ranks, name, days)
            ok += 1
            words += st["rows"]
            log(f"  [{i}/{len(todo)}] {cid} {name[:34]} : {st['rows']}개"
                + ("  (캐시)" if res.get("cached") else ""))
        except Exception as e:
            fail += 1
            log(f"  ! {cid} 실패: {str(e)[:70]}")
        if progress:
            progress(i, len(todo))
        if delay:
            time.sleep(delay)

    log(f"[데이터랩] 완료 {ok:,}개 카테고리 / 키워드 {words:,}개"
        f" / 빈값 {skip} / 실패 {fail}")
    return {"ok": ok, "fail": fail, "skip": skip, "keywords": words,
            "total": len(items), "todo": len(todo)}

def category_keywords_with_views(cid: str, days: int = 30, top: int = 500,
                                 batch: int = 100, log=print) -> list:
    """
    인기키워드에 조회수를 붙여 돌려준다.

    데이터랩은 순위만 준다. 태그는 '조회수 1000 미만' 을 우선해야 하므로
    enrich 로 검색량을 따로 받아 합친다. enrich 는 한 번에 다 못 받아
    나눠 부른다.

    반환: [{rank, keyword, views, pc_views, mobile_views, comp_idx,
            product_count}, ...]
    """
    res = category_keywords(cid, days=days) or {}
    ranks = (res.get("ranks") or [])[:top]
    if not ranks:
        return []

    # 네이버 검색광고는 키워드를 대문자로 정규화해서 갖고 있다. 소문자 라틴이
    # 섞인 채로 물으면 조회수가 그냥 0 으로 온다 (2026-09-05 실측).
    #   cctv모형 -> 0   /   CCTV모형 -> 1,100
    # 0 과 진짜 0 을 구분 못 하면 '1000 미만 우선' 규칙이 정반대로 뒤집힌다.
    words = [r["keyword"] for r in ranks if r.get("keyword")]
    info = {}
    for i in range(0, len(words), batch):
        chunk = [w.upper() for w in words[i:i + batch]]
        try:
            info.update(enrich(chunk) or {})
        except Exception as e:
            log(f"  ! 조회수 조회 실패 {i}~{i + len(chunk)}: {str(e)[:60]}")
    info = {k.upper(): v for k, v in info.items()}

    out = []
    for r in ranks:
        kw = (r.get("keyword") or "").strip()
        if not kw:
            continue
        d = info.get(kw.upper()) or {}
        pc = _num(d.get("monthlyPcQcCnt"))
        mo = _num(d.get("monthlyMobileQcCnt"))
        out.append({
            "rank": int(r.get("rank") or 0),
            "keyword": kw,
            "pc_views": pc,
            "mobile_views": mo,
            "views": (pc + mo) if (pc is not None and mo is not None) else None,
            "comp_idx": d.get("compIdx") or "",
            "product_count": _num(d.get("productCount")),
        })
    return out


def _num(v):
    """'< 10' 같은 값이 섞여 온다. 숫자만 뽑고 아니면 None."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"[0-9]+", str(v).replace(",", ""))
    return int(m.group()) if m else None
