"""
네이버 커머스 API — **스마트스토어 오늘 매출**을 실시간으로 받아온다.

주문 DB(`joacham.orders_order`)는 수집 주기가 있어 **오늘치가 비어 있다.**
오늘 얼마 팔렸는지는 스토어에서 직접 받아야 한다(2026-09-12 사용자).

인증은 OAuth2 지만 서명이 특이하다 — 100번 서버 `naverterms` 의
`smartstore/smartstore_product_service.py` 가 쓰는 방식 그대로다.

    sign = base64( bcrypt( f"{client_id}_{timestamp}", salt=client_secret ) )
    POST https://api.commerce.naver.com/external/v1/oauth2/token
         client_id, timestamp, client_secret_sign, grant_type=client_credentials,
         type=SELF

주문은 두 단계다. **목록은 ID 만 주고 내용은 따로 받아야 한다.**

    POST /external/v1/pay-order/seller/product-orders/last-changed-statuses
         lastChangedFrom, lastChangedTo   → productOrderId 목록
    POST /external/v1/pay-order/seller/product-orders/query
         productOrderIds[]                → 상품·금액·상태

토큰은 3시간쯤 살아 있으므로 메모리에 캐시한다.
"""
import base64
import datetime
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import config, db

BASE = "https://api.commerce.naver.com"
TOKEN_URL = BASE + "/external/v1/oauth2/token"
CHANGED = BASE + "/external/v1/pay-order/seller/product-orders/last-changed-statuses"
QUERY = BASE + "/external/v1/pay-order/seller/product-orders/query"

# 매출로 치지 않는 상태 (취소·반품·미결제)
DEAD = {"CANCELED", "CANCEL_DONE", "RETURNED", "RETURN_DONE",
        "PAYMENT_WAITING", "CANCELED_BY_NOPAYMENT"}

_token = {"value": "", "exp": 0.0}

# 커머스 API 는 **초당 호출 제한**이 있다. 여러 날짜를 잇달아 받으면
# `GW.RATE_LIMIT` 429 가 떨어진다 — 14일치를 한 번에 받다가 6일이 막혔다
# (2026-09-12). 호출 사이를 벌리고, 429 면 쉬었다 다시 건다.
MIN_GAP = 1.2          # 초
RETRY = 4
TAIL = 5               # 주문 뒤 며칠까지 상태변경을 더 훑을지
_last = [0.0]


def _wait():
    gap = time.time() - _last[0]
    if gap < MIN_GAP:
        time.sleep(MIN_GAP - gap)
    _last[0] = time.time()


def available() -> bool:
    return bool(config._str("COMMERCE_CLIENT_ID")
                and config._str("COMMERCE_CLIENT_SECRET"))


def _post(url, data, token=None, timeout=30, form=True):
    if form:
        body = urllib.parse.urlencode(data).encode()
        ct = "application/x-www-form-urlencoded"
    else:
        body = json.dumps(data).encode()
        ct = "application/json"
    h = {"Content-Type": ct, "Accept": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]


def token(force: bool = False) -> str:
    """접근 토큰. 살아 있으면 다시 받지 않는다."""
    if not force and _token["value"] and _token["exp"] > time.time() + 60:
        return _token["value"]
    import bcrypt
    cid = config._str("COMMERCE_CLIENT_ID")
    sec = config._str("COMMERCE_CLIENT_SECRET")
    ts = int(time.time() * 1000)
    sign = base64.b64encode(
        bcrypt.hashpw(f"{cid}_{ts}".encode(), sec.encode())).decode()
    st, d = _post(TOKEN_URL, {
        "client_id": cid, "timestamp": ts, "client_secret_sign": sign,
        "grant_type": "client_credentials", "type": "SELF"})
    if st != 200 or not isinstance(d, dict):
        raise RuntimeError(f"토큰 실패 {st} {str(d)[:160]}")
    _token["value"] = d.get("access_token") or ""
    _token["exp"] = time.time() + int(d.get("expires_in") or 10800)
    return _token["value"]


def _kst(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000+09:00")


def changed_ids(since, until=None, tok=None) -> list:
    """그 구간에 상태가 바뀐 상품주문 ID. 한 번에 24시간까지만 된다."""
    tok = tok or token()
    until = until or datetime.datetime.now()
    p = {"lastChangedFrom": _kst(since), "lastChangedTo": _kst(until)}
    for attempt in range(RETRY):
        _wait()
        req = urllib.request.Request(
            CHANGED + "?" + urllib.parse.urlencode(p),
            headers={"Authorization": "Bearer " + tok,
                     "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            break
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:200]
            if e.code == 429 and attempt < RETRY - 1:
                time.sleep(2 ** attempt * 2)        # 2 · 4 · 8초
                continue
            raise RuntimeError(f"변경조회 {e.code} {body}")
    else:
        raise RuntimeError("변경조회 재시도 초과")
    data = (d.get("data") or {})
    return [x.get("productOrderId") for x in
            (data.get("lastChangeStatuses") or []) if x.get("productOrderId")]


def details(ids: list, tok=None) -> list:
    """상품주문 상세. 한 번에 300개까지."""
    tok = tok or token()
    out = []
    for i in range(0, len(ids), 300):
        for attempt in range(RETRY):
            _wait()
            st, d = _post(QUERY, {"productOrderIds": ids[i:i + 300]},
                          token=tok, form=False)
            if st == 429 and attempt < RETRY - 1:
                time.sleep(2 ** attempt * 2)
                continue
            break
        if st != 200 or not isinstance(d, dict):
            raise RuntimeError(f"상세조회 {st} {str(d)[:200]}")
        out += (d.get("data") or [])
    return out


def sales(day=None, log=print) -> dict:
    """
    그날 매출. 반환 {day, orders, qty, amount, by_product:{상품번호:{...}}}

    `productOrder.totalPaymentAmount` 가 실결제금액이다. 취소·반품·미결제는
    뺀다. 같은 주문이 여러 번 바뀌어도 productOrderId 로 중복을 없앤다.
    """
    day = day or datetime.date.today()
    if isinstance(day, str):
        day = datetime.date.fromisoformat(day)
    start = datetime.datetime.combine(day, datetime.time.min)
    end = min(datetime.datetime.combine(day, datetime.time.max),
              datetime.datetime.now())
    tok = token()
    ids = changed_ids(start, end, tok)
    log(f"  {day} 변경 {len(ids):,}건")
    if not ids:
        return {"day": str(day), "orders": 0, "qty": 0, "amount": 0,
                "by_product": {}}
    rows = details(ids, tok)
    seen, by = set(), {}
    n = qty = amt = 0
    for r in rows:
        po = r.get("productOrder") or {}
        pid = po.get("productOrderId")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        if (po.get("productOrderStatus") or "") in DEAD:
            continue
        # 그날 주문한 것만 (상태만 바뀐 옛 주문은 뺀다)
        od = (r.get("order") or {}).get("orderDate") or ""
        if od[:10] and od[:10] != str(day):
            continue
        code = str(po.get("productId") or "")
        q = int(po.get("quantity") or 0)
        a = int(po.get("totalPaymentAmount") or 0)
        n += 1
        qty += q
        amt += a
        b = by.setdefault(code, {"orders": 0, "qty": 0, "amount": 0,
                                 "name": po.get("productName") or ""})
        b["orders"] += 1
        b["qty"] += q
        b["amount"] += a
    log(f"  {day} 주문 {n:,}건 · 수량 {qty:,} · {amt:,}원")
    return {"day": str(day), "orders": n, "qty": qty, "amount": amt,
            "by_product": by}


def sales_range(since, until=None, log=print) -> dict:
    """
    기간 매출을 **주문일 기준**으로 모은다. 반환 {날짜: sales()와 같은 꼴}

    `last-changed-statuses` 는 **상태가 바뀐 날**로 준다. 6일에 주문하고
    8일에 발송했으면 그 주문은 8일 목록에 뜬다. 날짜별로 따로 부르면서
    "주문일이 그날이 아니면 버린다" 고 하면 **그 주문은 어디에도 안 잡힌다**
    — 14일치를 그렇게 받았더니 6건밖에 안 나왔다(2026-09-12).

    그래서 구간 전체의 변경 목록을 모은 뒤 **주문일로 다시 나눈다.**
    뒤늦게 바뀐 것까지 담으려고 `until` 뒤로 며칠 더 훑는다.
    """
    if isinstance(since, str):
        since = datetime.date.fromisoformat(since)
    until = until or datetime.date.today()
    if isinstance(until, str):
        until = datetime.date.fromisoformat(until)
    tok = token()
    ids, day = [], since
    scan_to = min(until + datetime.timedelta(days=TAIL), datetime.date.today())
    while day <= scan_to:
        s = datetime.datetime.combine(day, datetime.time.min)
        e = min(datetime.datetime.combine(day, datetime.time.max),
                datetime.datetime.now())
        try:
            got = changed_ids(s, e, tok)
        except Exception as ex:
            log(f"  {day} 변경조회 실패: {str(ex)[:70]}")
            got = []
        ids += got
        day += datetime.timedelta(days=1)
    ids = list(dict.fromkeys(ids))
    log(f"  {since}~{scan_to} 변경 {len(ids):,}건")
    out = {}
    if not ids:
        return out
    seen = set()
    for r in details(ids, tok):
        po = r.get("productOrder") or {}
        pid = po.get("productOrderId")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        if (po.get("productOrderStatus") or "") in DEAD:
            continue
        d = ((r.get("order") or {}).get("orderDate") or "")[:10]
        if not d or d < str(since) or d > str(until):
            continue
        b = out.setdefault(d, {"day": d, "orders": 0, "qty": 0, "amount": 0,
                               "by_product": {}})
        code = str(po.get("productId") or "")
        q = int(po.get("quantity") or 0)
        a = int(po.get("totalPaymentAmount") or 0)
        b["orders"] += 1
        b["qty"] += q
        b["amount"] += a
        p2 = b["by_product"].setdefault(
            code, {"orders": 0, "qty": 0, "amount": 0,
                   "name": po.get("productName") or ""})
        p2["orders"] += 1
        p2["qty"] += q
        p2["amount"] += a
    return out


def save(res: dict, log=print) -> int:
    """`ad_sales` 에 넣는다 — 주문 DB 로 만든 것과 같은 표를 쓴다."""
    from . import ad_sales
    cmap = ad_sales._code_map()
    rows, now = [], db.now_str()
    for code, v in (res.get("by_product") or {}).items():
        key = cmap.get(code)
        if not key:
            continue
        rows.append((res["day"], key[0], key[1], v["orders"], v["qty"],
                     v["amount"], now))
    with db.sqlite_conn() as c:
        ad_sales._ddl(c)
        c.execute("DELETE FROM ad_sales WHERE day=?", (res["day"],))
        c.executemany(
            "INSERT OR REPLACE INTO ad_sales (day, lcp_code, l_code, orders,"
            " qty, amount, updated_at) VALUES (?,?,?,?,?,?,?)", rows)
    log(f"  광고상품 {len(rows)}행 · "
        f"{sum(r[5] for r in rows):,}원 저장")
    return len(rows)
