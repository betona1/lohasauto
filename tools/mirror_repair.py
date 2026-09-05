"""
이미 서버에 올라간 행들에 local_id 를 채워 넣는다 (1회성 보정).

증분 백필이 '어디까지 올렸는지' 판단하려면 서버 행이 로컬 행 번호를 알아야 한다.
초기 이관 때는 그 컬럼이 없었으므로, 자연키로 짝을 지어 채운다.

  scan       : (folder_name, scanned_at, mode)
  work_log   : (folder_name, ts)
  rate_log   : (folder_name, ts)
  cell/item  : 위에서 만든 scan 매핑 + 스캔 내부 순서
"""
import io
import sys
import traceback
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import db  # noqa: E402


def log(m):
    print(m, flush=True)


def norm(v):
    return "" if v is None else str(v)


def main():
    conn = db.mysql_conn()
    if conn is None:
        log("!! MySQL 연결 실패")
        return 1

    with db.sqlite_conn() as sq, conn:
        def local(sql, args=()):
            return [dict(r) for r in sq.execute(sql, args).fetchall()]

        with conn.cursor() as cur:
            db.mysql_migrate(cur)

            # ---------------- scan ----------------
            loc_scans = local("SELECT id, folder_name, scanned_at, mode "
                              "FROM scan ORDER BY id")
            buckets = defaultdict(list)
            for r in loc_scans:
                buckets[(norm(r["folder_name"]), norm(r["scanned_at"]),
                         norm(r["mode"]))].append(r["id"])

            cur.execute("SELECT id, folder_name, scanned_at, mode "
                        "FROM LOHASAUTO_SCAN WHERE local_id IS NULL ORDER BY id")
            srv_scans = cur.fetchall()

            scan_map = {}          # 서버 scan id -> 로컬 scan id
            miss = 0
            for sid, folder, sat, mode in srv_scans:
                key = (norm(folder), norm(sat).replace("T", " "), norm(mode))
                lst = buckets.get(key)
                if not lst:
                    miss += 1
                    continue
                lid = lst.pop(0)
                scan_map[sid] = lid
                cur.execute("UPDATE LOHASAUTO_SCAN SET local_id=%s WHERE id=%s",
                            (lid, sid))
            log(f"scan       : {len(scan_map)}건 매칭 / 실패 {miss}건")

            # 이미 local_id 가 있는 서버 스캔도 매핑에 포함시킨다
            cur.execute("SELECT id, local_id FROM LOHASAUTO_SCAN "
                        "WHERE local_id IS NOT NULL")
            for sid, lid in cur.fetchall():
                scan_map.setdefault(sid, lid)

            # ---------------- scan_cell / scan_item ----------------
            for mt, lt in (("LOHASAUTO_SCAN_CELL", "scan_cell"),
                           ("LOHASAUTO_SCAN_ITEM", "scan_item")):
                cur.execute(f"SELECT id, scan_id FROM {mt} "
                            f"WHERE local_id IS NULL ORDER BY id")
                rows = cur.fetchall()
                by_scan = defaultdict(list)
                for rid, sid in rows:
                    by_scan[sid].append(rid)

                fixed = skipped = 0
                for sid, rids in by_scan.items():
                    lid = scan_map.get(sid)
                    if lid is None:
                        skipped += len(rids)
                        continue
                    loc_ids = [r["id"] for r in local(
                        f"SELECT id FROM {lt} WHERE scan_id = ? ORDER BY id", (lid,))]
                    for rid, l in zip(rids, loc_ids):
                        cur.execute(f"UPDATE {mt} SET local_id=%s WHERE id=%s",
                                    (l, rid))
                        fixed += 1
                log(f"{lt:11}: {fixed}건 매칭 / 건너뜀 {skipped}건")

            # ---------------- work_log / rate_log ----------------
            for mt, lt in (("LOHASAUTO_WORK_LOG", "work_log"),
                           ("LOHASAUTO_RATE_LOG", "rate_log")):
                loc = local(f"SELECT id, folder_name, ts FROM {lt} ORDER BY id")
                b = defaultdict(list)
                for r in loc:
                    b[(norm(r["folder_name"]), norm(r["ts"]))].append(r["id"])

                cur.execute(f"SELECT id, folder_name, ts FROM {mt} "
                            f"WHERE local_id IS NULL ORDER BY id")
                fixed = miss = 0
                for rid, folder, ts in cur.fetchall():
                    key = (norm(folder), norm(ts).replace("T", " "))
                    lst = b.get(key)
                    if not lst:
                        miss += 1
                        continue
                    cur.execute(f"UPDATE {mt} SET local_id=%s WHERE id=%s",
                                (lst.pop(0), rid))
                    fixed += 1
                log(f"{lt:11}: {fixed}건 매칭 / 실패 {miss}건")

            # ---------------- 결과 ----------------
            log("-" * 58)
            for mt in db.MYSQL_LOCALID_TABLES:
                cur.execute(f"SELECT COUNT(*), COUNT(local_id) FROM {mt}")
                tot, has = cur.fetchone()
                log(f"   {mt:26} {has:>6}/{tot:<6} local_id 채움")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log(traceback.format_exc())
        sys.exit(1)
