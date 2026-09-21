"""
**DB 백업** — SQLite 통째로 + MySQL 쪽 lohasauto 관련 표.

DB 를 웹서버 것으로 통합하기 전에 먼저 떠 둔다(2026-09-21 사용자).
옮기다 잘못되면 되돌릴 자리가 있어야 한다.

    SQLite   sqlite3 온라인 백업 API — **앱이 켜져 있어도 안전**하다.
             파일 복사는 쓰는 중에 뜨면 깨진 파일이 나온다
    MySQL    mysqldump 가 이 PC 에 없어서 직접 만든다. gzip 으로 줄인다

대용량 미러 표(`LOHASAUTO_SCAN_ITEM` 519MB 등)는 **SQLite 에서 다시 만들
수 있으므로**(`tools/mirror_backfill.py`) 기본값에서는 스키마만 뜬다.
`--full` 을 주면 자료까지 전부 뜬다.

    python -X utf8 tools/db_backup.py                기본
    python -X utf8 tools/db_backup.py --full         큰 표까지 전부
    python -X utf8 tools/db_backup.py --only-mysql   MySQL 만
"""
import argparse
import datetime
import gzip
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config                                          # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "backup")

# 이 크기(MB)를 넘으면 자료는 건너뛰고 스키마만 뜬다 (--full 이면 무시)
BIG_MB = 60


def log(m):
    print(f"{datetime.datetime.now():%H:%M:%S}  {m}", flush=True)


def backup_sqlite(stamp: str) -> str:
    src = str(config.SQLITE_PATH)
    dst = os.path.join(OUT, f"lohasauto_{stamp}.db")
    if os.path.exists(dst):
        os.remove(dst)
    t = time.time()
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    s.backup(d)
    d.close()
    s.close()
    # 뜬 것이 멀쩡한지 확인한다 — 확인 안 한 백업은 백업이 아니다
    c = sqlite3.connect(dst)
    ok = c.execute("PRAGMA quick_check").fetchone()[0]
    n = len([r for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")])
    c.close()
    log(f"SQLite {os.path.getsize(dst) / 1024 / 1024:,.0f}MB · 표 {n}개 · "
        f"무결성 {ok} · {time.time() - t:.0f}초")
    return dst


def _conn():
    import pymysql
    return pymysql.connect(
        host=config._str("DB_HOST"), port=int(config._str("DB_PORT", "3306")),
        user=config._str("DB_USER"),
        password=config._str("DB_PASSWORD").strip("'\""),
        database=config._str("DB_NAME") or "ads",
        charset="utf8mb4", connect_timeout=15)


def _esc(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (bytes, bytearray)):
        return "0x" + v.hex() if v else "''"
    if isinstance(v, (datetime.datetime, datetime.date)):
        return "'" + str(v) + "'"
    s = str(v)
    for a, b in (("\\", "\\\\"), ("'", "\\'"), ("\n", "\\n"),
                 ("\r", "\\r"), ("\x00", "\\0")):
        s = s.replace(a, b)
    return "'" + s + "'"


def backup_mysql(stamp: str, full: bool = False) -> str:
    cn = _conn()
    name = config._str("DB_NAME") or "ads"
    dst = os.path.join(OUT, f"{name}_lohasauto_{stamp}.sql.gz")
    t = time.time()
    with cn.cursor() as c:
        c.execute(
            "SELECT table_name, ROUND((data_length+index_length)/1024/1024),"
            " table_rows FROM information_schema.tables"
            " WHERE table_schema=%s AND (table_name LIKE 'LOHASAUTO%%'"
            " OR table_name LIKE 'ads\\_%%') ORDER BY table_name", (name,))
        tabs = [(r[0], int(r[1] or 0), int(r[2] or 0)) for r in c.fetchall()]
    log(f"MySQL {name} — 표 {len(tabs)}개 "
        f"({sum(x[1] for x in tabs):,}MB)")

    done, skipped = 0, []
    with gzip.open(dst, "wt", encoding="utf-8") as f:
        f.write(f"-- lohasauto 백업 {datetime.datetime.now()}\n")
        f.write(f"-- {config._str('DB_HOST')}/{name}\n")
        f.write("SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS=0;\n\n")
        for tb, mb, rows in tabs:
            with cn.cursor() as c:
                c.execute(f"SHOW CREATE TABLE `{tb}`")
                f.write(f"\n-- ---- {tb} ({mb}MB, 약 {rows:,}행)\n")
                f.write(f"DROP TABLE IF EXISTS `{tb}`;\n")
                f.write(c.fetchone()[1] + ";\n")
            if mb > BIG_MB and not full:
                skipped.append(f"{tb}({mb}MB)")
                f.write(f"-- 자료 생략 — mirror_backfill.py 로 복구 가능\n")
                continue
            with cn.cursor() as c:
                c.execute(f"SELECT * FROM `{tb}`")
                cols = [d[0] for d in c.description]
                head = ("INSERT INTO `%s` (%s) VALUES\n"
                        % (tb, ",".join(f"`{x}`" for x in cols)))
                buf, n = [], 0
                while True:
                    batch = c.fetchmany(2000)
                    if not batch:
                        break
                    for row in batch:
                        buf.append("(" + ",".join(_esc(v) for v in row) + ")")
                        n += 1
                    f.write(head + ",\n".join(buf) + ";\n")
                    buf = []
            done += 1
            log(f"  {tb:<34} {n:>9,}행")
    cn.close()
    log(f"MySQL {os.path.getsize(dst) / 1024 / 1024:,.0f}MB · 자료 {done}개 · "
        f"{time.time() - t:.0f}초")
    if skipped:
        log("  자료 생략(큰 표): " + ", ".join(skipped))
        log("  → SQLite 가 원본입니다. tools/mirror_backfill.py 로 되만듭니다")
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="큰 표도 자료까지")
    ap.add_argument("--only-mysql", action="store_true")
    ap.add_argument("--only-sqlite", action="store_true")
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    log(f"백업 폴더 {OUT}")
    made = []
    if not args.only_mysql:
        made.append(backup_sqlite(stamp))
    if not args.only_sqlite:
        try:
            made.append(backup_mysql(stamp, args.full))
        except Exception as e:
            log(f"MySQL 백업 실패: {str(e)[:150]}")
    print()
    for p in made:
        log(f"완료 → {p}  ({os.path.getsize(p) / 1024 / 1024:,.0f}MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
