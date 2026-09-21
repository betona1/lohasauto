"""
**돈만 쓰고 안 팔리는 LCP 골라내기.**

광고비의 93% 가 매출 0원인 상품에 나가고 있다(2026-09-21 실측). 그것만
끄면 남는 돈이 달라진다 — 라고 말하기 전에, **정말 나쁜 상품인지**를
먼저 따져야 한다.

    계정 전체 전환율 1.21%   (클릭 1,488 → 주문 18)
    본전에 필요한 전환율 2.77%

전환율이 1.21% 라는 것은, **클릭 20번 받고 안 팔린 상품의 78% 는
'평균만큼 팔리는 정상 상품'** 이라는 뜻이다((1-0.0121)^20 = 0.785).
그러니 "클릭 몇 번 받고 안 팔렸으니 나쁘다" 고 단정하면 멀쩡한 상품을
끈다. 등급을 나눠 **확실한 것부터** 끈다.

    확실   기대 주문 1.0건 이상인데 0건      끌 근거가 있다
    의심   기대 주문 0.4~1.0건인데 0건       지켜보거나 입찰가만 낮춘다
    모름   그 아래                          아직 판단할 자료가 없다

'기대 주문' 은 `클릭 x 계정 전환율` 이다. 이 값이 1 을 넘는데도 0건이면
그 상품은 평균보다 확실히 못 판다.

    python -X utf8 tools/ad_dead.py                       기본(광고 시작일부터)
    python -X utf8 tools/ad_dead.py --since 2026-09-18    기간 지정
    python -X utf8 tools/ad_dead.py --excel               엑셀로 저장
    python -X utf8 tools/ad_dead.py --min-cost 2000       그 이상 쓴 것만
"""
import argparse
import datetime
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db                                              # noqa: E402
from app.lohas import ad_account, ad_profit                     # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")

GRADES = ("확실", "의심", "모름")


def gather(since: str, until: str) -> dict:
    """LCP 별 광고비·클릭·노출 + 매출. 하나로 합쳐 돌려준다."""
    with db.sqlite_conn() as c:
        cost = {r["lcp_code"]: dict(r) for r in c.execute(
            "SELECT lcp_code, SUM(cost) cost, SUM(clk) clk, SUM(imp) imp"
            " FROM ad_spend WHERE level='adgroup' AND lcp_code<>''"
            " AND day>=? AND day<=? GROUP BY lcp_code", (since, until))}
        sale = {r["lcp_code"]: dict(r) for r in c.execute(
            "SELECT lcp_code, SUM(amount) amount, SUM(orders) orders,"
            " SUM(qty) qty FROM ad_sales WHERE day>=? AND day<=?"
            " GROUP BY lcp_code", (since, until))}
        name = {r["lcp_code"]: r["product_name"] for r in c.execute(
            "SELECT lcp_code, MAX(product_name) product_name"
            " FROM ad_creative WHERE lcp_code<>'' GROUP BY lcp_code")}
        bid = {r["lcp_code"]: int(r["bid"] or 0) for r in c.execute(
            "SELECT lcp_code, MAX(bid) bid FROM ad_group"
            " WHERE lcp_code<>'' GROUP BY lcp_code")}

    tot_clk = sum(int(v.get("clk") or 0) for v in cost.values())
    tot_ord = sum(int(v.get("orders") or 0) for v in sale.values())
    cvr = (tot_ord / tot_clk) if tot_clk else 0.0

    rows = []
    for k, v in cost.items():
        c_ = int(v.get("cost") or 0)
        if c_ <= 0:
            continue
        s = sale.get(k, {})
        amt = int(s.get("amount") or 0)
        clk = int(v.get("clk") or 0)
        exp = clk * cvr                      # 평균만큼 팔렸다면 몇 건일까
        rows.append({
            "lcp_code": k, "name": name.get(k) or "",
            "cost": c_, "clk": clk, "imp": int(v.get("imp") or 0),
            "bid": bid.get(k, 0), "amount": amt,
            "orders": int(s.get("orders") or 0),
            "qty": int(s.get("qty") or 0),
            "roas": (amt * 100 // c_) if c_ else 0,
            "expect": round(exp, 2),
            "grade": ("" if amt > 0 else
                      "확실" if exp >= 1.0 else
                      "의심" if exp >= 0.4 else "모름"),
        })
    rows.sort(key=lambda r: -r["cost"])
    return {"rows": rows, "cvr": cvr, "clk": tot_clk, "orders": tot_ord}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="")
    ap.add_argument("--until", default="")
    ap.add_argument("--min-cost", type=int, default=0)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--excel", action="store_true")
    args = ap.parse_args()

    db.init_db()
    cu = (ad_account.main_account() or {}).get("customer_id") or ""
    since = args.since or ad_account.start_of(cu) or (
        datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    until = args.until or datetime.date.today().isoformat()

    g = gather(since, until)
    rows = [r for r in g["rows"] if r["cost"] >= args.min_cost]
    if not rows:
        print("자료가 없습니다 — 먼저 tools/ad_watch.py 를 돌리십시오.")
        return 1

    be = ad_profit.breakeven(since, until)
    tot = sum(r["cost"] for r in rows)
    live = [r for r in rows if r["amount"] > 0]
    aov = (sum(r["amount"] for r in live) // sum(r["orders"] for r in live)
           if live and sum(r["orders"] for r in live) else 0)
    # 주문 하나가 감당할 수 있는 광고비 = 객단가 x 마진율
    pay = aov * be["margin"] / 100 if aov else 0
    cpc = (tot // g["clk"]) if g["clk"] else 0
    need = (100 / (pay / cpc)) if (pay and cpc) else 0

    print(f"기간 {since} ~ {until}")
    print(f"광고비 {tot:,}원 · 클릭 {g['clk']:,} · CPC {cpc:,}원")
    print(f"주문 {g['orders']}건 · 객단가 {aov:,}원 · 마진 {be['margin']}%")
    print(f"전환율 {g['cvr'] * 100:.2f}%  ←→  본전에 필요한 전환율 "
          f"{need:.2f}%")
    print()
    print(f"광고비 쓴 LCP {len(rows):,}개")
    print(f"  매출 있음 {len(live):,}개 · 광고비 "
          f"{sum(r['cost'] for r in live):,}원 · 매출 "
          f"{sum(r['amount'] for r in live):,}원")
    for gd in GRADES:
        sel = [r for r in rows if r["grade"] == gd]
        if not sel:
            continue
        print(f"  매출 0원 [{gd}] {len(sel):,}개 · 광고비 "
              f"{sum(r['cost'] for r in sel):,}원 "
              f"({sum(r['cost'] for r in sel) * 100 // tot}%)")
    print()

    for gd in ("확실", "의심"):
        sel = [r for r in rows if r["grade"] == gd][:args.top]
        if not sel:
            continue
        print(f"=== [{gd}] 끌 후보 — 광고비 많은 순 ===")
        print("  LCP                 광고비   클릭  기대주문 입찰가  상품명")
        acc = 0
        for r in sel:
            acc += r["cost"]
            print(f"  {r['lcp_code']:20}{r['cost']:>7,}원 {r['clk']:>5} "
                  f"{r['expect']:>7.2f} {r['bid']:>5,}원  "
                  f"{(r['name'] or '')[:30]}")
        print(f"  ── 이 {len(sel)}개를 끄면 {acc:,}원 아낍니다 "
              f"(전체의 {acc * 100 // tot}%)")
        print()

    print("=== 팔린 LCP ===")
    print("  LCP                 광고비    매출  ROAS  주문  상품명")
    for r in sorted(live, key=lambda x: -x["amount"]):
        print(f"  {r['lcp_code']:20}{r['cost']:>7,}원{r['amount']:>8,}원 "
              f"{r['roas']:>5,}% {r['orders']:>4}  {(r['name'] or '')[:28]}")

    if args.excel:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "LCP별 광고성과"
        ws.append(["LCP", "상품명", "등급", "광고비", "노출", "클릭",
                   "입찰가", "기대주문", "매출", "주문", "수량", "ROAS"])
        for r in rows:
            ws.append([r["lcp_code"], r["name"], r["grade"] or "판매",
                       r["cost"], r["imp"], r["clk"], r["bid"], r["expect"],
                       r["amount"], r["orders"], r["qty"], r["roas"]])
        for col, w in zip("ABCDEFGHIJKL",
                          (20, 42, 7, 10, 9, 7, 8, 9, 10, 6, 6, 8)):
            ws.column_dimensions[col].width = w
        ws.freeze_panes = "A2"
        p = os.path.join("logs", f"LCP광고성과_{until}.xlsx")
        os.makedirs("logs", exist_ok=True)
        wb.save(p)
        print()
        print(f"엑셀로 저장했습니다 — {os.path.abspath(p)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
