"""
로컬 SQLite → 서버 MySQL 증분 백필.

서버 행마다 로컬 행 번호(local_id)를 들고 있으므로, '아직 안 올라간 것만'
골라서 올린다. 몇 번을 돌려도 중복이 생기지 않는다.

  folder / ss_analysis        : PK 기준 UPSERT
  scan / cell / item          : local_id 가 서버에 없는 행만
  work_log / rate_log         : local_id 가 서버에 없는 행만

부모 스캔은 올라갔는데 자식(cell/item)만 빠진 경우도 채운다.

사용:
  python tools/mirror_backfill.py            증분 업로드
  python tools/mirror_backfill.py --check    현황만 확인 (쓰기 없음)
"""
import io
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import config, db  # noqa: E402

CHECK_ONLY = "--check" in sys.argv
NO_DEDUP = "--no-dedup" in sys.argv
CHUNK = 500


def log(m):
    print(m, flush=True)


def server_local_ids(cur, table) -> set:
    cur.execute(f"SELECT local_id FROM `{table}` WHERE local_id IS NOT NULL")
    return {r[0] for r in cur.fetchall()}


def insert_many(cur, table, fields, rows) -> int:
    if not rows:
        return 0
    cols = ", ".join(f"`{c}`" for c in fields)
    marks = ", ".join(["%s"] * len(fields))
    sql = f"INSERT INTO `{table}` ({cols}) VALUES ({marks})"
    n = 0
    for i in range(0, len(rows), CHUNK):
        part = rows[i:i + CHUNK]
        cur.executemany(sql, [[r.get(c) for c in fields] for r in part])
        n += len(part)
    return n


def dedup(cur, dry: bool = False) -> dict:
    """
    구버전이 local_id 없이 미러한 행이 증분 업로드분과 겹칠 수 있다.
    local_id 가 비어 있으면서 자연키가 같은 행이 따로 있으면 그 쪽을 지운다.
    """
    out = {"scan": 0, "cell": 0, "item": 0, "work": 0, "rate": 0}

    # ---- scan (+ 자식) ----
    cur.execute("""
        SELECT a.id FROM LOHASAUTO_SCAN a
        JOIN LOHASAUTO_SCAN b
          ON a.folder_name = b.folder_name
         AND a.scanned_at  = b.scanned_at
         AND COALESCE(a.mode,'') = COALESCE(b.mode,'')
         AND b.local_id IS NOT NULL
        WHERE a.local_id IS NULL
    """)
    dup_ids = [r[0] for r in cur.fetchall()]
    if dup_ids:
        marks = ",".join(["%s"] * len(dup_ids))
        cur.execute(f"SELECT COUNT(*) FROM LOHASAUTO_SCAN_CELL "
                    f"WHERE scan_id IN ({marks})", dup_ids)
        out["cell"] = cur.fetchone()[0]
        cur.execute(f"SELECT COUNT(*) FROM LOHASAUTO_SCAN_ITEM "
                    f"WHERE scan_id IN ({marks})", dup_ids)
        out["item"] = cur.fetchone()[0]
        out["scan"] = len(dup_ids)
        if not dry:
            cur.execute(f"DELETE FROM LOHASAUTO_SCAN_CELL "
                        f"WHERE scan_id IN ({marks})", dup_ids)
            cur.execute(f"DELETE FROM LOHASAUTO_SCAN_ITEM "
                        f"WHERE scan_id IN ({marks})", dup_ids)
            cur.execute(f"DELETE FROM LOHASAUTO_SCAN "
                        f"WHERE id IN ({marks})", dup_ids)

    # ---- work_log / rate_log ----
    for key, t in (("work", "LOHASAUTO_WORK_LOG"), ("rate", "LOHASAUTO_RATE_LOG")):
        cur.execute(f"""
            SELECT a.id FROM {t} a
            JOIN {t} b ON a.folder_name = b.folder_name AND a.ts = b.ts
                      AND b.local_id IS NOT NULL
            WHERE a.local_id IS NULL
        """)
        ids = [r[0] for r in cur.fetchall()]
        out[key] = len(ids)
        if ids and not dry:
            marks = ",".join(["%s"] * len(ids))
            cur.execute(f"DELETE FROM {t} WHERE id IN ({marks})", ids)

    return out


def main():
    if not config.MYSQL_ENABLED:
        log("!! .env 의 MYSQL_ENABLED=1 로 먼저 켜주세요.")
        return 1
    conn = db.mysql_conn()
    if conn is None:
        log("!! MySQL 연결 실패")
        return 1

    done = {}
    with db.sqlite_conn() as sq, conn:
        def local(sql, args=()):
            return [dict(r) for r in sq.execute(sql, args).fetchall()]

        with conn.cursor() as cur:
            for ddl in db.MYSQL_DDL:
                cur.execute(ddl)
            db.mysql_migrate(cur)

            if not NO_DEDUP:
                removed = dedup(cur, dry=CHECK_ONLY)
                if removed:
                    log(f"중복 정리 : 스캔 {removed['scan']}건 "
                        f"(칸 {removed['cell']}, 상세 {removed['item']}) / "
                        f"work_log {removed['work']}건 / "
                        f"rate_log {removed['rate']}건"
                        + ("  ← --check 라 실제 삭제는 안 함" if CHECK_ONLY else ""))

            # ---------- 1) folder (UPSERT) ----------
            folders = local("SELECT * FROM folder")
            f_cols = ("name", "raw_label", "option_value", "site_count", "source",
                      "sort_order", "is_active", "is_work", "is_job",
                      "first_seen_at", "last_seen_at")
            if not CHECK_ONLY and folders:
                upd = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in f_cols[1:])
                cur.executemany(
                    f"INSERT INTO `LOHASAUTO_FOLDER` "
                    f"({', '.join('`' + c + '`' for c in f_cols)}) "
                    f"VALUES ({', '.join(['%s'] * len(f_cols))}) "
                    f"ON DUPLICATE KEY UPDATE {upd}",
                    [[r.get(c) for c in f_cols] for r in folders])
            done["FOLDER (upsert)"] = len(folders)

            # ---------- 2) ss_analysis (UPSERT) ----------
            analyses = local("SELECT * FROM ss_analysis")
            a_cols = db.ANALYSIS_FIELDS + ("created_at", "updated_at")
            if not CHECK_ONLY and analyses:
                upd = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in a_cols[1:])
                cur.executemany(
                    f"INSERT INTO `LOHASAUTO_SS_ANALYSIS` "
                    f"({', '.join('`' + c + '`' for c in a_cols)}) "
                    f"VALUES ({', '.join(['%s'] * len(a_cols))}) "
                    f"ON DUPLICATE KEY UPDATE {upd}",
                    [[r.get(c) for c in a_cols] for r in analyses])
            done["SS_ANALYSIS (upsert)"] = len(analyses)

            # ---------- 3) scan (증분) ----------
            have_scan = server_local_ids(cur, "LOHASAUTO_SCAN")
            new_scans = [r for r in local("SELECT * FROM scan ORDER BY id")
                         if r["id"] not in have_scan]
            log(f"scan      : 신규 {len(new_scans)}건 (서버 보유 {len(have_scan)})")

            scan_map = {}      # 로컬 scan id -> 서버 scan id
            cur.execute("SELECT local_id, id FROM LOHASAUTO_SCAN "
                        "WHERE local_id IS NOT NULL")
            scan_map.update({lid: sid for lid, sid in cur.fetchall()})

            if not CHECK_ONLY:
                cols = db.SCAN_FIELDS
                for r in new_scans:
                    cur.execute(
                        f"INSERT INTO `LOHASAUTO_SCAN` "
                        f"({', '.join('`' + c + '`' for c in cols)}, `local_id`) "
                        f"VALUES ({', '.join(['%s'] * (len(cols) + 1))})",
                        [r.get(c) for c in cols] + [r["id"]])
                    scan_map[r["id"]] = cur.lastrowid
            done["SCAN"] = len(new_scans)

            # ---------- 4) scan_cell / scan_item (증분) ----------
            for mt, lt, fields in (
                    ("LOHASAUTO_SCAN_CELL", "scan_cell", db.CELL_FIELDS),
                    ("LOHASAUTO_SCAN_ITEM", "scan_item", db.ITEM_FIELDS)):
                have = server_local_ids(cur, mt)
                rows = [r for r in local(f"SELECT * FROM {lt} ORDER BY id")
                        if r["id"] not in have]
                ready, orphan = [], 0
                for r in rows:
                    sid = scan_map.get(r["scan_id"])
                    if sid is None:
                        orphan += 1
                        continue
                    ready.append(dict(r, scan_id=sid, local_id=r["id"]))
                log(f"{lt:10}: 신규 {len(ready)}건"
                    + (f" (부모 스캔 없음 {orphan}건 보류)" if orphan else ""))
                if not CHECK_ONLY:
                    insert_many(cur, mt,
                                ("scan_id",) + fields + ("created_at", "local_id"),
                                ready)
                done[mt.replace("LOHASAUTO_", "")] = len(ready)

            # ---------- 5) work_log / rate_log (증분) ----------
            for mt, lt, fields in (
                    ("LOHASAUTO_WORK_LOG", "work_log", db.WORK_LOG_FIELDS),
                    ("LOHASAUTO_RATE_LOG", "rate_log", db.RATE_LOG_FIELDS)):
                have = server_local_ids(cur, mt)
                rows = [dict(r, local_id=r["id"])
                        for r in local(f"SELECT * FROM {lt} ORDER BY id")
                        if r["id"] not in have]
                log(f"{lt:10}: 신규 {len(rows)}건")
                if not CHECK_ONLY:
                    insert_many(cur, mt, fields + ("local_id",), rows)
                done[mt.replace("LOHASAUTO_", "")] = len(rows)

    log("-" * 58)
    log("확인만 함 (--check)" if CHECK_ONLY else "업로드 완료")
    for k, v in done.items():
        log(f"   {k:22} {v:>6}행")

    # ---------- 검증 ----------
    conn2 = db.mysql_conn()
    with db.sqlite_conn() as sq, conn2:
        with conn2.cursor() as cur:
            log("-" * 58)
            for lt, mt in (("folder", "LOHASAUTO_FOLDER"),
                           ("ss_analysis", "LOHASAUTO_SS_ANALYSIS"),
                           ("scan", "LOHASAUTO_SCAN"),
                           ("scan_cell", "LOHASAUTO_SCAN_CELL"),
                           ("scan_item", "LOHASAUTO_SCAN_ITEM"),
                           ("work_log", "LOHASAUTO_WORK_LOG"),
                           ("rate_log", "LOHASAUTO_RATE_LOG")):
                ln = sq.execute(f"SELECT COUNT(*) n FROM {lt}").fetchone()["n"]
                cur.execute(f"SELECT COUNT(*) FROM `{mt}`")
                mn = cur.fetchone()[0]
                mark = "OK" if mn >= ln else f"부족 {ln - mn}"
                log(f"   {lt:12} 로컬 {ln:>6} / 서버 {mn:>6}   {mark}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log(traceback.format_exc())
        sys.exit(1)
