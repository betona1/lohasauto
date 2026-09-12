"""
자체 DB 레이어.

- 기본 저장소 : SQLite (data/lohasauto.db) - 서버 없이 단독 동작
- 선택 미러  : MySQL (.env 의 MYSQL_ENABLED=1 일 때만)

MySQL 미러링이 실패해도 SQLite 저장은 항상 성공하도록 설계했다.
(미러 실패는 예외를 올리지 않고 경고 문자열로만 돌려준다)
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Iterable, Optional

from . import config

# ---------------------------------------------------------------- DDL (SQLite)

SQLITE_DDL = [
    # 마스터(작업폴더) 목록 : 사이트에서 스캔해 저장
    """
    CREATE TABLE IF NOT EXISTS folder (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT    NOT NULL UNIQUE,   -- '594. 광고진행-비트마인드'
        raw_label     TEXT,                      -- '594. 광고진행-비트마인드(764)'
        option_value  TEXT,                      -- select option 의 value
        site_count    INTEGER,                   -- 라벨 괄호안 수량 (764)
        source        TEXT,                      -- 스캔 출처 페이지
        sort_order    INTEGER DEFAULT 0,         -- 사이트 콤보 순서
        is_active     INTEGER NOT NULL DEFAULT 1,-- 마지막 스캔에 존재했는지
        is_work       INTEGER NOT NULL DEFAULT 0,-- 마스터폴더(작업리스트) 소속
        is_job        INTEGER NOT NULL DEFAULT 0,-- 작업폴더(점검 대상, 단일)
        first_seen_at TEXT,
        last_seen_at  TEXT
    )
    """,
    # 점검 1회 = 1행 (요약)
    """
    CREATE TABLE IF NOT EXISTS scan (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        folder_name  TEXT NOT NULL,
        scanned_at   TEXT NOT NULL,
        mode         TEXT NOT NULL DEFAULT 'full',  -- full=12칸 전체 / quick=작업대상만

        total_rows   INTEGER NOT NULL DEFAULT 0,  -- 전체 행(L코드) 수
        total_lcps   INTEGER NOT NULL DEFAULT 0,  -- 전체 고유 LCP 수

        img_todo_rows  INTEGER NOT NULL DEFAULT 0,  -- 대표이미지 미작업
        img_work_rows  INTEGER NOT NULL DEFAULT 0,  -- 대표이미지 이미지작업(승인전)
        img_done_rows  INTEGER NOT NULL DEFAULT 0,  -- 대표이미지 승인완료

        info_todo_rows    INTEGER NOT NULL DEFAULT 0,  -- 상품정보 미작업
        info_save_rows    INTEGER NOT NULL DEFAULT 0,  -- 상품정보 저장완료
        info_exclude_rows INTEGER NOT NULL DEFAULT 0,  -- 상품정보 제외
        info_hold_rows    INTEGER NOT NULL DEFAULT 0,  -- 상품정보 보류

        target_rows  INTEGER NOT NULL DEFAULT 0,  -- ★ 승인완료+미작업 행수
        target_lcps  INTEGER NOT NULL DEFAULT 0,  -- ★ 승인완료+미작업 LCP수

        capped       INTEGER NOT NULL DEFAULT 0,  -- 조회상한에 걸린 칸이 있었는지
        elapsed_sec  REAL,
        note         TEXT
    )
    """,
    """CREATE INDEX IF NOT EXISTS idx_scan_folder ON scan(folder_name, scanned_at)""",
    # 상태 조합(대표이미지 x 상품정보) 매트릭스 각 칸
    """
    CREATE TABLE IF NOT EXISTS scan_cell (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id      INTEGER NOT NULL,
        folder_name  TEXT,
        image_status TEXT,
        info_status  TEXT,
        row_count    INTEGER NOT NULL DEFAULT 0,
        lcp_count    INTEGER NOT NULL DEFAULT 0,
        capped       INTEGER NOT NULL DEFAULT 0,
        is_target    INTEGER NOT NULL DEFAULT 0,
        created_at   TEXT,
        FOREIGN KEY(scan_id) REFERENCES scan(id) ON DELETE CASCADE
    )
    """,
    """CREATE INDEX IF NOT EXISTS idx_cell_scan ON scan_cell(scan_id)""",
    # 작업대상 상세행
    """
    CREATE TABLE IF NOT EXISTS scan_item (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id      INTEGER NOT NULL,
        folder_name  TEXT,
        bucket       TEXT,          -- 'target'
        lcp_code     TEXT,
        l_code       TEXT,
        image_status TEXT,
        info_status  TEXT,
        created_at   TEXT,
        FOREIGN KEY(scan_id) REFERENCES scan(id) ON DELETE CASCADE
    )
    """,
    """CREATE INDEX IF NOT EXISTS idx_item_scan ON scan_item(scan_id)""",
    """CREATE INDEX IF NOT EXISTS idx_item_lcp ON scan_item(lcp_code)""",
    # 상품분석 실행 이력 : LCP 단위로 완료 상태를 최신화해 재실행을 막는다
    """
    CREATE TABLE IF NOT EXISTS ss_analysis (
        lcp_code     TEXT PRIMARY KEY,
        folder_name  TEXT,
        product_no   TEXT,          -- 팝업 no (commercial_ss_image_attr)
        product_id   TEXT,          -- startAnalysis 의 product_id
        analysis_no  TEXT,          -- 분석서버가 돌려준 작업번호
        status       TEXT,          -- done / pending / error / skip
        state_msg    TEXT,
        analyzed_at  TEXT,          -- 분석완료 시각 (팝업 analysis_date)
        created_at   TEXT,
        updated_at   TEXT
    )
    """,
    """CREATE INDEX IF NOT EXISTS idx_ssa_status ON ss_analysis(status)""",
    """CREATE INDEX IF NOT EXISTS idx_ssa_folder ON ss_analysis(folder_name)""",
    # 모니터링이 찾아둔 '미분석 LCP' 대기열 : ALL 상품분석이 검색 없이 바로 쓴다
    """
    CREATE TABLE IF NOT EXISTS analysis_queue (
        lcp_code    TEXT PRIMARY KEY,
        folder_name TEXT,
        l_code      TEXT,
        product_no  TEXT,          -- 상품정보 팝업 no (분석 요청에 필요)
        found_at    TEXT,
        updated_at  TEXT
    )
    """,
    """CREATE INDEX IF NOT EXISTS idx_queue_folder ON analysis_queue(folder_name)""",
    # 자동점검이 남기는 작업 변동 로그 (시간당 처리량 계산 / 그래프용)
    """
    CREATE TABLE IF NOT EXISTS work_log (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        ts             TEXT NOT NULL,
        folder_name    TEXT NOT NULL,
        total_rows     INTEGER, total_lcps    INTEGER,
        img_done_rows  INTEGER, img_work_rows INTEGER,
        info_save_rows INTEGER, info_todo_rows INTEGER,
        target_lcps    INTEGER, analyzed_lcps INTEGER, pending_lcps INTEGER,
        d_img_done     INTEGER, d_info_save   INTEGER,
        d_info_todo    INTEGER, d_analyzed    INTEGER, d_pending INTEGER,
        elapsed_sec    REAL
    )
    """,
    """CREATE INDEX IF NOT EXISTS idx_wl_ts ON work_log(folder_name, ts)""",
    # 처리속도 로그 : 30분/1시간 처리량과 10개당 소요시간을 주기적으로 남긴다
    """
    CREATE TABLE IF NOT EXISTS rate_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        ts           TEXT NOT NULL,
        folder_name  TEXT NOT NULL,
        m30_info     INTEGER, m30_img INTEGER, m30_analyzed INTEGER,
        h1_info      INTEGER, h1_img  INTEGER, h1_analyzed  INTEGER,
        per10_info   REAL,          -- 상품정보완료 10개당 분
        per10_img    REAL,
        per10_analyzed REAL,
        pending_lcps INTEGER,
        eta_min      REAL           -- 남은 미완료를 다 끝내는 데 걸릴 예상 분
    )
    """,
    """CREATE INDEX IF NOT EXISTS idx_rl_ts ON rate_log(folder_name, ts)""",
    # 상품 편집 작업 로그 (카테고리/태그/상품명 단계별 기록)
    """
    CREATE TABLE IF NOT EXISTS task_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        ts           TEXT NOT NULL,
        folder_name  TEXT,
        lcp_code     TEXT,
        l_code       TEXT,
        product_no   TEXT,
        step         TEXT,          -- analysis / category / tag / title1 ...
        action       TEXT,          -- read / pick / save / skip
        status       TEXT,          -- ok / skip / fail / preview
        picked       TEXT,          -- 고른 키워드 (콤마)
        candidates   INTEGER,       -- 후보 개수
        picked_count INTEGER,
        source       TEXT,          -- gemini / rule
        message      TEXT,
        elapsed_sec  REAL
    )
    """,
    """CREATE INDEX IF NOT EXISTS idx_tl_ts ON task_log(folder_name, ts)""",
    """CREATE INDEX IF NOT EXISTS idx_tl_lcp ON task_log(lcp_code, step)""",
    # ---- LCP 단위 수집 (포함상품보기 / 키워드관리 / 카테고리) ----
    """
    CREATE TABLE IF NOT EXISTS lcp_product (
        lcp_code     TEXT PRIMARY KEY,
        folder_name  TEXT,
        product_id   TEXT,
        product_no   TEXT,
        product_name TEXT,
        brand        TEXT,
        maker        TEXT,
        origin       TEXT,
        cost         TEXT,
        markets      TEXT,        -- 마켓별 카테고리 JSON
        wish_keywords TEXT,       -- 희망검색어 (공백구분)
        option_count INTEGER,
        used_count   INTEGER,
        rec_count    INTEGER,
        cat_count    INTEGER,
        token_count  INTEGER,
        collected_at TEXT,
        updated_at   TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS lcp_option (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        lcp_code  TEXT NOT NULL,
        seq       INTEGER,
        name      TEXT,           -- 선택01~20 제품명
        subs      TEXT,           -- 하위옵션 JSON
        created_at TEXT
    )
    """,
    """CREATE INDEX IF NOT EXISTS idx_lopt ON lcp_option(lcp_code, seq)""",
    """
    CREATE TABLE IF NOT EXISTS lcp_keyword (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        lcp_code  TEXT NOT NULL,
        keyword   TEXT NOT NULL,
        source    TEXT,           -- used(지마켓용) / recommend / wish
        views     INTEGER,
        auction   INTEGER,
        gmarket   INTEGER,
        total     INTEGER,
        created_at TEXT
    )
    """,
    """CREATE INDEX IF NOT EXISTS idx_lkw ON lcp_keyword(lcp_code, source)""",
    """CREATE INDEX IF NOT EXISTS idx_lkw_word ON lcp_keyword(keyword)""",
    """
    CREATE TABLE IF NOT EXISTS lcp_category (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        lcp_code  TEXT NOT NULL,
        code      TEXT,
        name      TEXT,
        cnt       INTEGER,
        unit      TEXT,
        capacity  TEXT,
        rank      INTEGER,
        created_at TEXT
    )
    """,
    """CREATE INDEX IF NOT EXISTS idx_lcat ON lcp_category(lcp_code, cnt)""",
    # L코드 단위 상태 (대표이미지 / 상품정보)
    """
    CREATE TABLE IF NOT EXISTS lcp_lcode (
        lcp_code    TEXT NOT NULL,
        l_code      TEXT NOT NULL,
        folder_name TEXT,
        product_no  TEXT,
        img_status  TEXT,
        info_status TEXT,
        updated_at  TEXT,
        PRIMARY KEY (lcp_code, l_code)
    )
    """,
    """CREATE INDEX IF NOT EXISTS idx_llc_folder ON lcp_lcode(folder_name)""",
    """CREATE INDEX IF NOT EXISTS idx_llc_status ON lcp_lcode(img_status, info_status)""",
    # 상품(L코드)별 작업 상세 : 카테고리 / 속성 / 상품명·태그
    """
    CREATE TABLE IF NOT EXISTS lcode_attr (
        product_no    TEXT PRIMARY KEY,
        lcp_code      TEXT,
        l_code        TEXT,
        folder_name   TEXT,
        etc_category  TEXT,
        analysis_date TEXT,
        analysis_done INTEGER,
        cat_saved     INTEGER,
        attr_saved    INTEGER,
        title_saved   INTEGER,
        title1        TEXT,
        title_count   INTEGER,
        tag_count     INTEGER,
        attribute_count INTEGER,
        titles        TEXT,
        tags          TEXT,
        next_step     TEXT,
        updated_at    TEXT
    )
    """,
    """CREATE INDEX IF NOT EXISTS idx_la_lcp ON lcode_attr(lcp_code)""",
    """CREATE INDEX IF NOT EXISTS idx_la_step ON lcode_attr(folder_name, next_step)""",
    # 네이버 데이터랩 카테고리별 인기키워드 (최근 30일, 카테고리당 최대 500)
    # 로하스가 주는 후보(LCP당 수십 개)만으로는 부족해 후보 풀을 넓히는 용도다.
    """
    CREATE TABLE IF NOT EXISTS datalab_keyword (
        cid        TEXT NOT NULL,
        rank       INTEGER NOT NULL,
        keyword    TEXT,
        cat_name   TEXT,
        days       INTEGER,
        collected_at TEXT,
        PRIMARY KEY (cid, rank)
    )
    """,
    # 로하스가 키워드를 형태소로 쪼갤 때 표기를 대표어로 바꾼다.
    #   relKeyword '스텐브러쉬'  ->  terms ['스테인리스', '브러쉬']
    # 그래서 '스텐...' 을 눌러도 이미 '스테인리스' 가 있으면 아무것도 안 들어간다.
    # 그 대응을 모아두면 어떤 키워드가 헛클릭인지 미리 알 수 있다.
    """
    CREATE TABLE IF NOT EXISTS synonym (
        surface    TEXT NOT NULL,          -- 화면에 보이는 표기 (스텐)
        normal     TEXT NOT NULL,          -- 로하스가 쓰는 대표어 (스테인리스)
        seen       INTEGER NOT NULL DEFAULT 1,
        example    TEXT,                   -- 어느 키워드에서 나왔나
        first_seen TEXT,
        last_seen  TEXT,
        PRIMARY KEY (surface, normal)
    )
    """,
    """CREATE INDEX IF NOT EXISTS idx_syn_normal ON synonym(normal)""",
    # 사람이 만든 상품명에서 배운 것. 원상품명에 없던 말을 카테고리별로 센다.
    #   '데코용품' -> 장식(113) 트리(107) 오너먼트(83) 꾸미기(47)
    #   '발매트'   -> 매트(26) 발판(22) 현관(20) 미끄럼방지(7)
    # 그 카테고리 상품에는 이런 말을 넣는다는 뜻이라, 후보를 고를 때 우선한다.
    """
    CREATE TABLE IF NOT EXISTS title_pattern (
        cid    TEXT NOT NULL,
        word   TEXT NOT NULL,
        n      INTEGER NOT NULL DEFAULT 0,   -- 이 카테고리에서 쓰인 횟수
        titles INTEGER NOT NULL DEFAULT 0,   -- 이 카테고리의 학습 대상 상품명 수
        updated_at TEXT,
        PRIMARY KEY (cid, word)
    )
    """,
    """CREATE INDEX IF NOT EXISTS idx_tp_word ON title_pattern(word)""",
    # 사용자가 지적한 것을 **카테고리별로** 쌓는다. 같은 카테고리 상품이
    # 다시 나오면 그대로 적용한다 (2026-09-06 지시).
    #   ban       그 말을 쓰지 않는다 (원상품명에 있으면 맨 뒤로만)
    #   need_name 원상품명에 있을 때만 쓴다
    #   must      될 수 있으면 넣는다
    """
    CREATE TABLE IF NOT EXISTS cat_rule (
        cid    TEXT NOT NULL,          -- 카테고리 코드 ('' = 전체)
        scope  TEXT NOT NULL,          -- title / tag / both
        word   TEXT NOT NULL,
        action TEXT NOT NULL,          -- ban / need_name / must
        note   TEXT,                   -- 사용자가 한 말 그대로
        created_at TEXT,
        PRIMARY KEY (cid, scope, word, action)
    )
    """,
    """CREATE INDEX IF NOT EXISTS idx_catrule_cid ON cat_rule(cid)""",
    # 프로그램 설정 한 줄짜리 값들 (품절 이동 폴더 등)
    """
    CREATE TABLE IF NOT EXISTS app_setting (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT
    )
    """,
    # 수정사항 1.0 일괄수정 이력. 어느 LCP 를 언제 처리했고 무엇을 건너뛰었나.
    # 전상품품절은 사이트가 저장을 막으므로 시도하지 않고 'soldout' 으로 남긴다.
    """
    CREATE TABLE IF NOT EXISTS fix10_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id     TEXT,
        folder_name TEXT,
        product_code TEXT,
        product_id TEXT,
        direction  TEXT,          -- asc(정방향) / desc(역방향)
        result     TEXT,          -- ok / soldout / fail
        message    TEXT,
        seconds    REAL,
        created_at TEXT
    )
    """,
    # AI 이미지 일괄수정 이력. **어느 폴더 몇 페이지를 이미 돌렸나** 를
    # 남겨 두어야 같은 페이지를 두 번 걸지 않는다(2026-09-07 사용자 요청).
    """
    CREATE TABLE IF NOT EXISTS ai_image_job (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        folder_name TEXT NOT NULL,
        page       INTEGER NOT NULL,
        job_no     TEXT,
        title      TEXT,
        query      TEXT,
        row_count  INTEGER,
        first_no   TEXT,
        last_no    TEXT,
        state      TEXT,
        started_at TEXT,
        ended_at   TEXT,
        created_at TEXT,
        UNIQUE (folder_name, page, title)
    )
    """,
    """CREATE INDEX IF NOT EXISTS idx_aiimg_folder
           ON ai_image_job(folder_name, page)""",
    # 상품명을 다시 만들기 전에 **지금 값을 그대로** 남겨 둔다.
    # 일괄로 돌리면 되돌릴 방법이 있어야 한다(2026-09-07 사용자 요청).
    """
    CREATE TABLE IF NOT EXISTS title_backup (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id     TEXT,
        folder_name TEXT,
        lcp_code   TEXT,
        l_code     TEXT NOT NULL,
        product_no TEXT,
        etc_category TEXT,
        title1     TEXT,
        product_name TEXT,
        reason     TEXT,
        created_at TEXT
    )
    """,
    """CREATE INDEX IF NOT EXISTS idx_tbak_run ON title_backup(run_id)""",
    """CREATE INDEX IF NOT EXISTS idx_tbak_l ON title_backup(l_code)""",
    """CREATE INDEX IF NOT EXISTS idx_fix10_run ON fix10_log(run_id)""",
    """CREATE INDEX IF NOT EXISTS idx_fix10_code ON fix10_log(product_code)""",
    # 품절이라 다른 임의분류로 넘긴 내역. 나중에 "왜 이 상품이 여기 있지" 를
    # 되짚을 수 있어야 한다 (2026-09-06 사용자 요청).
    """
    CREATE TABLE IF NOT EXISTS soldout_move (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT,
        from_folder TEXT,
        to_folder   TEXT,
        product_code TEXT,
        product_id  TEXT,
        title       TEXT,
        state       TEXT,
        result      TEXT
    )
    """,
    """CREATE INDEX IF NOT EXISTS idx_smove_code ON soldout_move(product_code)""",
    """CREATE INDEX IF NOT EXISTS idx_smove_ts ON soldout_move(ts)""",
    """CREATE INDEX IF NOT EXISTS idx_dlk_word ON datalab_keyword(keyword)""",
    """CREATE INDEX IF NOT EXISTS idx_dlk_cid ON datalab_keyword(cid, rank)""",
    # 로하스 태그/상품명 탭에서 긁은 키워드. 카테고리(cid)가 잡힌 LCP 만 가능하다
    # (카테고리가 없으면 서버가 표를 안 만들고 500 을 준다).
    """
    CREATE TABLE IF NOT EXISTS cat_keyword (
        cid        TEXT,
        lcp_code   TEXT NOT NULL,
        product_no TEXT,
        kind       TEXT NOT NULL,        -- tag | title
        title_no   INTEGER DEFAULT 0,    -- 상품명 탭 번호 (태그는 0)
        keyword    TEXT NOT NULL,
        views      INTEGER,
        banned     TEXT,                 -- 금지어 분류 (비어있으면 사용 가능)
        used       TEXT,                 -- 사용여부 칸 원문
        prio       INTEGER,              -- 태그사전/추천 가중치
        is_dict    INTEGER,
        is_rec     INTEGER,
        collected_at TEXT,
        PRIMARY KEY (lcp_code, kind, title_no, keyword)
    )
    """,
    """CREATE INDEX IF NOT EXISTS idx_ck_cid ON cat_keyword(cid, kind, views)""",
    """CREATE INDEX IF NOT EXISTS idx_ck_word ON cat_keyword(keyword)""",
]

# ---------------------------------------------------------------- DDL (MySQL)

MYSQL_DDL = [
    """
    CREATE TABLE IF NOT EXISTS LOHASAUTO_AI_IMAGE_JOB (
        local_id    INT PRIMARY KEY,
        folder_name VARCHAR(191) NOT NULL,
        page        INT NOT NULL,
        job_no      VARCHAR(20),
        title       VARCHAR(191),
        query       VARCHAR(500),
        row_count   INT,
        first_no    VARCHAR(30),
        last_no     VARCHAR(30),
        state       VARCHAR(30),
        started_at  VARCHAR(30),
        ended_at    VARCHAR(30),
        created_at  VARCHAR(30),
        KEY idx_folder_page (folder_name, page)
    ) DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS `LOHASAUTO_CAT_KEYWORD` (
        `cid`        VARCHAR(30)  DEFAULT NULL,
        `lcp_code`   VARCHAR(60)  NOT NULL,
        `product_no` VARCHAR(30)  DEFAULT NULL,
        `kind`       VARCHAR(10)  NOT NULL,
        `title_no`   INT NOT NULL DEFAULT 0,
        `keyword`    VARCHAR(190) NOT NULL,
        `views`      INT DEFAULT NULL,
        `banned`     VARCHAR(190) DEFAULT NULL,
        `used`       VARCHAR(60)  DEFAULT NULL,
        `prio`       INT DEFAULT NULL,
        `is_dict`    TINYINT(1) DEFAULT 0,
        `is_rec`     TINYINT(1) DEFAULT 0,
        `collected_at` VARCHAR(30) DEFAULT NULL,
        PRIMARY KEY (`lcp_code`,`kind`,`title_no`,`keyword`),
        KEY `idx_ck_cid` (`cid`,`kind`),
        KEY `idx_ck_word` (`keyword`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS `LOHASAUTO_DATALAB_KEYWORD` (
        `cid`          VARCHAR(30) NOT NULL,
        `rank`         INT NOT NULL,
        `keyword`      VARCHAR(255) DEFAULT NULL,
        `cat_name`     VARCHAR(500) DEFAULT NULL,
        `days`         INT DEFAULT NULL,
        `collected_at` VARCHAR(30) DEFAULT NULL,
        PRIMARY KEY (`cid`, `rank`),
        KEY `idx_dlk_word` (`keyword`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS `LOHASAUTO_FOLDER` (
        `name`          VARCHAR(255) NOT NULL,
        `raw_label`     VARCHAR(300) DEFAULT NULL,
        `option_value`  VARCHAR(255) DEFAULT NULL,
        `site_count`    INT DEFAULT NULL,
        `source`        VARCHAR(50)  DEFAULT NULL,
        `sort_order`    INT NOT NULL DEFAULT 0,
        `is_active`     TINYINT(1) NOT NULL DEFAULT 1,
        `is_work`       TINYINT(1) NOT NULL DEFAULT 0,
        `is_job`        TINYINT(1) NOT NULL DEFAULT 0,
        `first_seen_at` DATETIME DEFAULT NULL,
        `last_seen_at`  DATETIME DEFAULT NULL,
        PRIMARY KEY (`name`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `LOHASAUTO_SCAN` (
        `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        `folder_name`  VARCHAR(255) NOT NULL,
        `scanned_at`   DATETIME NOT NULL,
        `mode`         VARCHAR(10) NOT NULL DEFAULT 'full',
        `total_rows`   INT NOT NULL DEFAULT 0,
        `total_lcps`   INT NOT NULL DEFAULT 0,
        `img_todo_rows` INT NOT NULL DEFAULT 0,
        `img_work_rows` INT NOT NULL DEFAULT 0,
        `img_done_rows` INT NOT NULL DEFAULT 0,
        `info_todo_rows`    INT NOT NULL DEFAULT 0,
        `info_save_rows`    INT NOT NULL DEFAULT 0,
        `info_exclude_rows` INT NOT NULL DEFAULT 0,
        `info_hold_rows`    INT NOT NULL DEFAULT 0,
        `target_rows`  INT NOT NULL DEFAULT 0,
        `target_lcps`  INT NOT NULL DEFAULT 0,
        `capped`       TINYINT(1) NOT NULL DEFAULT 0,
        `elapsed_sec`  DOUBLE DEFAULT NULL,
        `note`         VARCHAR(500) DEFAULT NULL,
        `local_id`     BIGINT DEFAULT NULL,
        PRIMARY KEY (`id`),
        UNIQUE KEY `uq_local` (`local_id`),
        KEY `idx_folder` (`folder_name`, `scanned_at`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `LOHASAUTO_SCAN_CELL` (
        `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        `scan_id`      BIGINT UNSIGNED NOT NULL,
        `folder_name`  VARCHAR(255) DEFAULT NULL,
        `image_status` VARCHAR(30)  DEFAULT NULL,
        `info_status`  VARCHAR(30)  DEFAULT NULL,
        `row_count`    INT NOT NULL DEFAULT 0,
        `lcp_count`    INT NOT NULL DEFAULT 0,
        `capped`       TINYINT(1) NOT NULL DEFAULT 0,
        `is_target`    TINYINT(1) NOT NULL DEFAULT 0,
        `created_at`   DATETIME DEFAULT NULL,
        `local_id`     BIGINT DEFAULT NULL,
        PRIMARY KEY (`id`),
        UNIQUE KEY `uq_local` (`local_id`),
        KEY `idx_scan` (`scan_id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `LOHASAUTO_SCAN_ITEM` (
        `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        `scan_id`      BIGINT UNSIGNED NOT NULL,
        `folder_name`  VARCHAR(255) DEFAULT NULL,
        `bucket`       VARCHAR(20)  DEFAULT NULL,
        `lcp_code`     VARCHAR(50)  DEFAULT NULL,
        `l_code`       VARCHAR(50)  DEFAULT NULL,
        `image_status` VARCHAR(30)  DEFAULT NULL,
        `info_status`  VARCHAR(30)  DEFAULT NULL,
        `created_at`   DATETIME DEFAULT NULL,
        `local_id`     BIGINT DEFAULT NULL,
        PRIMARY KEY (`id`),
        UNIQUE KEY `uq_local` (`local_id`),
        KEY `idx_scan` (`scan_id`),
        KEY `idx_lcp` (`lcp_code`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `LOHASAUTO_SS_ANALYSIS` (
        `lcp_code`    VARCHAR(50) NOT NULL,
        `folder_name` VARCHAR(255) DEFAULT NULL,
        `product_no`  VARCHAR(30)  DEFAULT NULL,
        `product_id`  VARCHAR(30)  DEFAULT NULL,
        `analysis_no` VARCHAR(30)  DEFAULT NULL,
        `status`      VARCHAR(20)  DEFAULT NULL,
        `state_msg`   VARCHAR(200) DEFAULT NULL,
        `analyzed_at` DATETIME     DEFAULT NULL,
        `created_at`  DATETIME     DEFAULT NULL,
        `updated_at`  DATETIME     DEFAULT NULL,
        PRIMARY KEY (`lcp_code`),
        KEY `idx_status` (`status`),
        KEY `idx_folder` (`folder_name`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `LOHASAUTO_WORK_LOG` (
        `id`             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        `ts`             DATETIME NOT NULL,
        `folder_name`    VARCHAR(255) NOT NULL,
        `total_rows`     INT, `total_lcps`    INT,
        `img_done_rows`  INT, `img_work_rows` INT,
        `info_save_rows` INT, `info_todo_rows` INT,
        `target_lcps`    INT, `analyzed_lcps` INT, `pending_lcps` INT,
        `d_img_done`     INT, `d_info_save`   INT,
        `d_info_todo`    INT, `d_analyzed`    INT, `d_pending` INT,
        `elapsed_sec`    DOUBLE,
        `local_id`       BIGINT DEFAULT NULL,
        PRIMARY KEY (`id`),
        UNIQUE KEY `uq_local` (`local_id`),
        KEY `idx_ts` (`folder_name`, `ts`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `LOHASAUTO_LCP_PRODUCT` (
        `lcp_code`      VARCHAR(50) NOT NULL,
        `folder_name`   VARCHAR(255) DEFAULT NULL,
        `product_id`    VARCHAR(30)  DEFAULT NULL,
        `product_no`    VARCHAR(30)  DEFAULT NULL,
        `product_name`  VARCHAR(500) DEFAULT NULL,
        `brand`         VARCHAR(200) DEFAULT NULL,
        `maker`         VARCHAR(200) DEFAULT NULL,
        `origin`        VARCHAR(200) DEFAULT NULL,
        `cost`          VARCHAR(50)  DEFAULT NULL,
        `markets`       TEXT         DEFAULT NULL,
        `wish_keywords` TEXT         DEFAULT NULL,
        `option_count`  INT, `used_count` INT, `rec_count` INT,
        `cat_count`     INT, `token_count` INT,
        `collected_at`  DATETIME DEFAULT NULL,
        `updated_at`    DATETIME DEFAULT NULL,
        PRIMARY KEY (`lcp_code`),
        KEY `idx_folder` (`folder_name`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `LOHASAUTO_LCP_OPTION` (
        `id`       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        `lcp_code` VARCHAR(50) NOT NULL,
        `seq`      INT, `name` VARCHAR(500), `subs` TEXT,
        `created_at` DATETIME DEFAULT NULL,
        `local_id` BIGINT DEFAULT NULL,
        PRIMARY KEY (`id`), UNIQUE KEY `uq_local` (`local_id`),
        KEY `idx_lcp` (`lcp_code`, `seq`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `LOHASAUTO_LCP_KEYWORD` (
        `id`       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        `lcp_code` VARCHAR(50) NOT NULL,
        `keyword`  VARCHAR(200) NOT NULL,
        `source`   VARCHAR(20), `views` INT,
        `auction`  INT, `gmarket` INT, `total` INT,
        `created_at` DATETIME DEFAULT NULL,
        `local_id` BIGINT DEFAULT NULL,
        PRIMARY KEY (`id`), UNIQUE KEY `uq_local` (`local_id`),
        KEY `idx_lcp` (`lcp_code`, `source`), KEY `idx_kw` (`keyword`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `LOHASAUTO_LCP_LCODE` (
        `lcp_code`    VARCHAR(50) NOT NULL,
        `l_code`      VARCHAR(50) NOT NULL,
        `folder_name` VARCHAR(255) DEFAULT NULL,
        `product_no`  VARCHAR(30)  DEFAULT NULL,
        `img_status`  VARCHAR(30)  DEFAULT NULL,
        `info_status` VARCHAR(30)  DEFAULT NULL,
        `updated_at`  DATETIME     DEFAULT NULL,
        PRIMARY KEY (`lcp_code`, `l_code`),
        KEY `idx_folder` (`folder_name`),
        KEY `idx_status` (`img_status`, `info_status`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `LOHASAUTO_LCODE_ATTR` (
        `product_no`    VARCHAR(30) NOT NULL,
        `lcp_code`      VARCHAR(50)  DEFAULT NULL,
        `l_code`        VARCHAR(50)  DEFAULT NULL,
        `folder_name`   VARCHAR(255) DEFAULT NULL,
        `etc_category`  VARCHAR(30)  DEFAULT NULL,
        `analysis_date` VARCHAR(30)  DEFAULT NULL,
        `analysis_done` TINYINT(1), `cat_saved` TINYINT(1),
        `attr_saved`    TINYINT(1), `title_saved` TINYINT(1),
        `title1`        VARCHAR(500) DEFAULT NULL,
        `title_count`   INT, `tag_count` INT, `attribute_count` INT,
        `titles`        TEXT, `tags` TEXT,
        `next_step`     VARCHAR(20)  DEFAULT NULL,
        `updated_at`    DATETIME     DEFAULT NULL,
        PRIMARY KEY (`product_no`),
        KEY `idx_lcp` (`lcp_code`),
        KEY `idx_step` (`folder_name`, `next_step`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `LOHASAUTO_SYNONYM` (
        `surface`    VARCHAR(100) NOT NULL,
        `normal`     VARCHAR(100) NOT NULL,
        `seen`       INT DEFAULT 1,
        `example`    VARCHAR(200) DEFAULT NULL,
        `first_seen` DATETIME DEFAULT NULL,
        `last_seen`  DATETIME DEFAULT NULL,
        PRIMARY KEY (`surface`, `normal`),
        KEY `idx_normal` (`normal`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `LOHASAUTO_LCP_CATEGORY` (
        `id`       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        `lcp_code` VARCHAR(50) NOT NULL,
        `code`     VARCHAR(30), `name` VARCHAR(300), `cnt` INT,
        `unit`     VARCHAR(50), `capacity` VARCHAR(50), `rank` INT,
        `created_at` DATETIME DEFAULT NULL,
        `local_id` BIGINT DEFAULT NULL,
        PRIMARY KEY (`id`), UNIQUE KEY `uq_local` (`local_id`),
        KEY `idx_lcp` (`lcp_code`, `cnt`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `LOHASAUTO_TASK_LOG` (
        `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        `ts`           DATETIME NOT NULL,
        `folder_name`  VARCHAR(255) DEFAULT NULL,
        `lcp_code`     VARCHAR(50)  DEFAULT NULL,
        `l_code`       VARCHAR(50)  DEFAULT NULL,
        `product_no`   VARCHAR(30)  DEFAULT NULL,
        `step`         VARCHAR(20)  DEFAULT NULL,
        `action`       VARCHAR(20)  DEFAULT NULL,
        `status`       VARCHAR(20)  DEFAULT NULL,
        `picked`       TEXT         DEFAULT NULL,
        `candidates`   INT DEFAULT NULL,
        `picked_count` INT DEFAULT NULL,
        `source`       VARCHAR(20)  DEFAULT NULL,
        `message`      VARCHAR(500) DEFAULT NULL,
        `elapsed_sec`  DOUBLE DEFAULT NULL,
        `local_id`     BIGINT DEFAULT NULL,
        PRIMARY KEY (`id`),
        UNIQUE KEY `uq_local` (`local_id`),
        KEY `idx_ts` (`folder_name`, `ts`),
        KEY `idx_lcp` (`lcp_code`, `step`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS `LOHASAUTO_RATE_LOG` (
        `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        `ts`          DATETIME NOT NULL,
        `folder_name` VARCHAR(255) NOT NULL,
        `m30_info` INT, `m30_img` INT, `m30_analyzed` INT,
        `h1_info`  INT, `h1_img`  INT, `h1_analyzed`  INT,
        `per10_info` DOUBLE, `per10_img` DOUBLE, `per10_analyzed` DOUBLE,
        `pending_lcps` INT, `eta_min` DOUBLE,
        `local_id`     BIGINT DEFAULT NULL,
        PRIMARY KEY (`id`),
        UNIQUE KEY `uq_local` (`local_id`),
        KEY `idx_ts` (`folder_name`, `ts`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
]


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- SQLite

_initialized = False


def _connect() -> sqlite3.Connection:
    config.SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 기본 대기 5초로는 모자란다. 수집·상품명 작업이 몇 분씩 쓰기를 잡고
    # 있으면 화면·트레이 쪽 조회가 'database is locked' 로 조용히 실패했다
    # (2026-09-07 트레이에 어제 숫자가 남아 있던 원인 중 하나).
    conn = sqlite3.connect(str(config.SQLITE_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def sqlite_conn():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# 기존 DB 에 나중에 추가된 컬럼들 (컬럼명 -> ALTER 정의)
_MIGRATIONS = {
    "folder": {
        "is_job": "INTEGER NOT NULL DEFAULT 0",
    },
    "scan": {
        "mode": "TEXT NOT NULL DEFAULT 'full'",
    },
    "lcp_product": {
        "token_count": "INTEGER",
    },
    # 데이터랩은 순위만 준다. 조회수는 enrich 로 따로 받아 같이 저장한다.
    # 태그는 조회수 1000 미만을 우선해야 해서 순위만으로는 고를 수 없다.
    "datalab_keyword": {
        "views": "INTEGER",
        "pc_views": "INTEGER",
        "mobile_views": "INTEGER",
        "comp_idx": "TEXT",
        "product_count": "INTEGER",
    },
}


def _migrate(conn) -> None:
    """기존 DB 에 없는 컬럼을 추가 (데이터 보존)."""
    for table, cols in _MIGRATIONS.items():
        try:
            have = {r["name"] for r in
                    conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except Exception:
            continue
        if not have:
            continue
        for col, ddl in cols.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def init_db() -> None:
    """SQLite 스키마 생성 + 마이그레이션 (앱 시작 시 1회)."""
    global _initialized
    with sqlite_conn() as conn:
        for ddl in SQLITE_DDL:
            conn.execute(ddl)
        _migrate(conn)
    _initialized = True


# ---------------------------------------------------------------- MySQL 미러

def mysql_conn():
    """MySQL 연결. 비활성/실패 시 None."""
    if not config.MYSQL_ENABLED:
        return None
    if not config.MYSQL.get("host"):
        return None          # 외부망에서 주소를 비워둔 경우 — 미러만 끈다
    try:
        import pymysql
    except ImportError:
        return None
    try:
        return pymysql.connect(
            host=config.MYSQL["host"],
            port=config.MYSQL["port"],
            user=config.MYSQL["user"],
            password=config.MYSQL["password"],
            db=config.MYSQL["db"],
            charset="utf8mb4",
            autocommit=True,
        )
    except Exception:
        return None


MYSQL_LOCALID_TABLES = ("LOHASAUTO_SCAN", "LOHASAUTO_SCAN_CELL",
                        "LOHASAUTO_SCAN_ITEM", "LOHASAUTO_WORK_LOG",
                        "LOHASAUTO_RATE_LOG", "LOHASAUTO_TASK_LOG",
                        "LOHASAUTO_LCP_OPTION", "LOHASAUTO_LCP_KEYWORD",
                        "LOHASAUTO_LCP_CATEGORY")


def mysql_migrate(cur) -> None:
    """이미 만들어진 서버 테이블에 local_id 컬럼/인덱스를 추가한다."""
    for t in MYSQL_LOCALID_TABLES:
        try:
            cur.execute(f"ALTER TABLE `{t}` "
                        f"ADD COLUMN IF NOT EXISTS `local_id` BIGINT DEFAULT NULL")
        except Exception:
            pass
        try:
            cur.execute(f"ALTER TABLE `{t}` "
                        f"ADD UNIQUE KEY IF NOT EXISTS `uq_local` (`local_id`)")
        except Exception:
            pass


def mysql_prepare(conn) -> None:
    """DDL + 마이그레이션을 한 번에."""
    with conn.cursor() as cur:
        for ddl in MYSQL_DDL:
            cur.execute(ddl)
        mysql_migrate(cur)


def mysql_status() -> str:
    """UI 표시용 MySQL 연결 상태 문자열."""
    if not config.MYSQL_ENABLED:
        return "MySQL 미러: 꺼짐"
    if not config.MYSQL.get("host"):
        return (f"MySQL 미러: 사용 안 함 ({config.net_profile()} 프로파일에 "
                "주소가 없습니다 - 로컬 SQLite 에만 저장)")
    conn = mysql_conn()
    if conn is None:
        return f"MySQL 미러: 연결실패 ({config.MYSQL['host']})"
    try:
        with conn:
            mysql_prepare(conn)
        return f"MySQL 미러: 연결됨 ({config.MYSQL['host']}/{config.MYSQL['db']})"
    except Exception as e:
        return f"MySQL 미러: 오류 ({e})"


# ---------------------------------------------------------------- 폴더 저장/조회

def save_folders(folders: Iterable[dict], source: str = "ss_image") -> dict:
    """
    스캔한 마스터 폴더 목록 저장(UPSERT).
    folders: [{name, raw_label, option_value, site_count}, ...]
    반환: {'total', 'new', 'updated', 'deactivated', 'mirror'}
    """
    folders = list(folders)
    ts = now_str()
    names = [f["name"] for f in folders]

    new_cnt = 0
    with sqlite_conn() as conn:
        existing = {
            r["name"] for r in conn.execute("SELECT name FROM folder").fetchall()
        }
        for order, f in enumerate(folders):
            if f["name"] not in existing:
                new_cnt += 1
            conn.execute(
                """
                INSERT INTO folder
                    (name, raw_label, option_value, site_count, source,
                     sort_order, is_active, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    raw_label    = excluded.raw_label,
                    option_value = excluded.option_value,
                    site_count   = excluded.site_count,
                    source       = excluded.source,
                    sort_order   = excluded.sort_order,
                    is_active    = 1,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    f["name"], f.get("raw_label"), f.get("option_value"),
                    f.get("site_count"), source, order, ts, ts,
                ),
            )

        # 이번 스캔에 없던 폴더는 비활성 처리 (삭제하지 않음 - 이력 보존)
        if names:
            placeholders = ",".join("?" * len(names))
            cur = conn.execute(
                f"UPDATE folder SET is_active = 0 "
                f"WHERE is_active = 1 AND name NOT IN ({placeholders})",
                names,
            )
            deactivated = cur.rowcount or 0
        else:
            deactivated = 0

    mirror = _mirror_folders(folders, source, ts)
    return {
        "total": len(folders),
        "new": new_cnt,
        "updated": len(folders) - new_cnt,
        "deactivated": deactivated,
        "mirror": mirror,
    }


def _mirror_folders(folders: list, source: str, ts: str) -> str:
    conn = mysql_conn()
    if conn is None:
        return "" if not config.MYSQL_ENABLED else "MySQL 미러 실패(연결)"
    sql = """
    INSERT INTO LOHASAUTO_FOLDER
        (name, raw_label, option_value, site_count, source,
         sort_order, is_active, first_seen_at, last_seen_at)
    VALUES (%s, %s, %s, %s, %s, %s, 1, %s, %s)
    ON DUPLICATE KEY UPDATE
        raw_label = VALUES(raw_label), option_value = VALUES(option_value),
        site_count = VALUES(site_count), source = VALUES(source),
        sort_order = VALUES(sort_order), is_active = 1,
        last_seen_at = VALUES(last_seen_at)
    """
    try:
        with conn:
            with conn.cursor() as cur:
                for ddl in MYSQL_DDL:
                    cur.execute(ddl)
                cur.executemany(sql, [
                    (f["name"], f.get("raw_label"), f.get("option_value"),
                     f.get("site_count"), source, i, ts, ts)
                    for i, f in enumerate(folders)
                ])
        return f"MySQL 미러 {len(folders)}건"
    except Exception as e:
        return f"MySQL 미러 실패: {e}"


def list_folders(active_only: bool = True) -> list:
    sql = """
        SELECT f.*,
               (SELECT scanned_at   FROM scan s WHERE s.folder_name = f.name
                 ORDER BY s.id DESC LIMIT 1) AS last_scan_at,
               (SELECT target_rows FROM scan s WHERE s.folder_name = f.name
                 ORDER BY s.id DESC LIMIT 1) AS last_target
        FROM folder f
    """
    if active_only:
        sql += " WHERE f.is_active = 1"
    sql += " ORDER BY f.sort_order, f.name"
    with sqlite_conn() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def _mirror_work_flag(name: str, on: bool) -> None:
    conn = mysql_conn()
    if conn is None:
        return
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE LOHASAUTO_FOLDER SET is_work = %s WHERE name = %s",
                    (1 if on else 0, name),
                )
    except Exception:
        pass


def add_master_folder(name: str) -> None:
    """마스터폴더(작업리스트)에 추가."""
    with sqlite_conn() as conn:
        conn.execute("UPDATE folder SET is_work = 1 WHERE name = ?", (name,))
    _mirror_work_flag(name, True)


def remove_master_folder(name: str) -> None:
    """마스터폴더에서 제외. 작업폴더였다면 그것도 해제."""
    with sqlite_conn() as conn:
        conn.execute(
            "UPDATE folder SET is_work = 0, is_job = 0 WHERE name = ?", (name,))
    _mirror_work_flag(name, False)


def list_master_folders() -> list:
    """마스터폴더로 지정된 폴더명 목록."""
    with sqlite_conn() as conn:
        rows = conn.execute(
            "SELECT name FROM folder WHERE is_work = 1 ORDER BY sort_order, name"
        ).fetchall()
    return [r["name"] for r in rows]


def set_job_folder(name: str) -> None:
    """작업폴더 지정 (단일). 마스터폴더가 아니면 함께 편입한다."""
    with sqlite_conn() as conn:
        conn.execute("UPDATE folder SET is_job = 0 WHERE is_job = 1")
        conn.execute(
            "UPDATE folder SET is_job = 1, is_work = 1 WHERE name = ?", (name,))
    conn2 = mysql_conn()
    if conn2 is not None:
        try:
            with conn2:
                with conn2.cursor() as cur:
                    cur.execute("UPDATE LOHASAUTO_FOLDER SET is_job = 0 WHERE is_job = 1")
                    cur.execute(
                        "UPDATE LOHASAUTO_FOLDER SET is_job = 1, is_work = 1 "
                        "WHERE name = %s", (name,))
        except Exception:
            pass


def save_soldout_moves(rows: list, from_folder: str, to_folder: str,
                       result: str = "ok") -> int:
    """
    품절 이동 내역을 남긴다. 로컬 SQLite + 서버 MySQL.

    rows 는 fix10.parse_rows() 가 준 그대로 — product_code/product_id/title/state.
    """
    ts = now_str()
    vals = [(ts, from_folder, to_folder, r.get("product_code", ""),
             str(r.get("product_id", "")), (r.get("title") or "")[:120],
             r.get("state", ""), result) for r in (rows or [])]
    if not vals:
        return 0
    with sqlite_conn() as c:
        c.executemany(
            "INSERT INTO soldout_move (ts, from_folder, to_folder, "
            "product_code, product_id, title, state, result) "
            "VALUES (?,?,?,?,?,?,?,?)", vals)
    conn = mysql_conn()
    if conn is not None:
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "CREATE TABLE IF NOT EXISTS `LOHASAUTO_SOLDOUT_MOVE` ("
                        "`id` INT AUTO_INCREMENT PRIMARY KEY,"
                        "`ts` VARCHAR(20), `from_folder` VARCHAR(120),"
                        "`to_folder` VARCHAR(120), `product_code` VARCHAR(40),"
                        "`product_id` VARCHAR(20), `title` VARCHAR(200),"
                        "`state` VARCHAR(40), `result` VARCHAR(20),"
                        "KEY(`product_code`), KEY(`ts`))"
                        " DEFAULT CHARSET=utf8mb4")
                    cur.executemany(
                        "INSERT INTO LOHASAUTO_SOLDOUT_MOVE (ts, from_folder,"
                        " to_folder, product_code, product_id, title, state,"
                        " result) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", vals)
        except Exception:
            pass
    return len(vals)


def soldout_moves(limit: int = 100, code: str = "") -> list:
    """품절로 넘긴 내역 조회. 코드로 찾으면 그 상품 이력만."""
    sql = "SELECT * FROM soldout_move"
    a = []
    if code:
        sql += " WHERE product_code = ?"
        a.append(code)
    sql += " ORDER BY id DESC LIMIT ?"
    a.append(limit)
    with sqlite_conn() as c:
        return [dict(r) for r in c.execute(sql, a)]


def set_setting(key: str, value: str) -> None:
    """
    설정 한 줄 저장. 품절 이동 폴더처럼 사용자마다 다른 값에 쓴다.

    로컬 SQLite 와 서버 MySQL 양쪽에 넣는다 — 다른 PC 에서도 같은 값을
    쓰기 위해서다(2026-09-06 사용자 지시). 미러가 안 되어도 로컬은 남는다.
    """
    ts = now_str()
    with sqlite_conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS app_setting ("
                  "key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
        c.execute("INSERT OR REPLACE INTO app_setting (key, value, updated_at)"
                  " VALUES (?,?,?)", (key, str(value or ""), ts))
    conn = mysql_conn()
    if conn is not None:
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "CREATE TABLE IF NOT EXISTS `LOHASAUTO_SETTING` ("
                        "`key` VARCHAR(64) NOT NULL PRIMARY KEY,"
                        "`value` TEXT, `updated_at` VARCHAR(20))"
                        " DEFAULT CHARSET=utf8mb4")
                    cur.execute(
                        "REPLACE INTO LOHASAUTO_SETTING (`key`,`value`,"
                        "`updated_at`) VALUES (%s,%s,%s)",
                        (key, str(value or ""), ts))
        except Exception:
            pass


# ------------------------------------------------------------ AI 이미지 작업
# 걸어둔 AI 이미지 일괄작업 한 건을 여기에 적어둔다. **예약된 작업이 있을
# 때만** 로하스 작업결과 화면을 본다 - 없는데 매 주기 긁으면 헛일이다
# (2026-09-07 사용자: "불필요한 로하스 크롤링은 자제하자").
AI_JOB_KEY = "ai_job_watch"


def ai_job_set(rec: dict) -> None:
    """진행 중인 AI 이미지 작업을 기록한다(폴링하는 쪽이 갱신)."""
    import json

    rec = dict(rec or {})
    rec["updated_at"] = now_str()
    set_setting(AI_JOB_KEY, json.dumps(rec, ensure_ascii=False))


def ai_job_get() -> dict:
    """걸어둔 AI 이미지 작업. 없으면 빈 dict."""
    import json

    v = get_setting(AI_JOB_KEY, "")
    if not v:
        return {}
    try:
        return json.loads(v)
    except Exception:
        return {}


def ai_job_clear() -> None:
    set_setting(AI_JOB_KEY, "")


def get_setting(key: str, default: str = "") -> str:
    try:
        with sqlite_conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS app_setting ("
                      "key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
            r = c.execute("SELECT value FROM app_setting WHERE key = ?",
                          (key,)).fetchone()
        return (r["value"] if r else "") or default
    except Exception:
        return default


def get_job_folder() -> Optional[str]:
    """현재 작업폴더 (점검 대상)."""
    with sqlite_conn() as conn:
        row = conn.execute(
            "SELECT name FROM folder WHERE is_job = 1 LIMIT 1").fetchone()
    return row["name"] if row else None


def get_work_folder() -> Optional[str]:
    """하위호환: 작업폴더 -> 없으면 첫 마스터폴더."""
    return get_job_folder() or (list_master_folders() or [None])[0]


# ---------------------------------------------------------------- 점검 저장/조회

SCAN_FIELDS = (
    "folder_name", "scanned_at", "mode", "total_rows", "total_lcps",
    "img_todo_rows", "img_work_rows", "img_done_rows",
    "info_todo_rows", "info_save_rows", "info_exclude_rows", "info_hold_rows",
    "target_rows", "target_lcps", "capped", "elapsed_sec", "note",
)

CELL_FIELDS = (
    "folder_name", "image_status", "info_status",
    "row_count", "lcp_count", "capped", "is_target",
)

ITEM_FIELDS = (
    "folder_name", "bucket", "lcp_code", "l_code", "image_status", "info_status",
)


def save_scan(summary: dict, cells: list, items: list) -> dict:
    """점검 결과 저장 (요약 1행 + 매트릭스 12칸 + 작업대상 상세행)."""
    ts = summary.get("scanned_at") or now_str()
    summary = dict(summary, scanned_at=ts)

    cols = ", ".join(SCAN_FIELDS)
    marks = ", ".join("?" * len(SCAN_FIELDS))
    with sqlite_conn() as conn:
        cur = conn.execute(
            f"INSERT INTO scan ({cols}) VALUES ({marks})",
            [summary.get(k) for k in SCAN_FIELDS],
        )
        scan_id = cur.lastrowid

        ccols = ", ".join(("scan_id",) + CELL_FIELDS + ("created_at",))
        cmarks = ", ".join("?" * (len(CELL_FIELDS) + 2))
        conn.executemany(
            f"INSERT INTO scan_cell ({ccols}) VALUES ({cmarks})",
            [[scan_id] + [c.get(k) for k in CELL_FIELDS] + [ts] for c in cells],
        )

        icols = ", ".join(("scan_id",) + ITEM_FIELDS + ("created_at",))
        imarks = ", ".join("?" * (len(ITEM_FIELDS) + 2))
        conn.executemany(
            f"INSERT INTO scan_item ({icols}) VALUES ({imarks})",
            [[scan_id] + [it.get(k) for k in ITEM_FIELDS] + [ts] for it in items],
        )
        # 미러에서 행을 1:1 대응시키기 위해 방금 저장된 로컬 id 를 읽어둔다
        cell_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM scan_cell WHERE scan_id = ? ORDER BY id", (scan_id,))]
        item_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM scan_item WHERE scan_id = ? ORDER BY id", (scan_id,))]

    mirror = _mirror_scan(summary, cells, items, ts,
                          scan_id, cell_ids, item_ids)
    return {"scan_id": scan_id, "cells": len(cells),
            "items": len(items), "mirror": mirror}


def _mirror_scan(summary: dict, cells: list, items: list, ts: str,
                 local_scan_id=None, cell_ids=None, item_ids=None) -> str:
    conn = mysql_conn()
    if conn is None:
        return "" if not config.MYSQL_ENABLED else "MySQL 미러 실패(연결)"
    try:
        with conn:
            mysql_prepare(conn)
            with conn.cursor() as cur:
                cols = ", ".join(f"`{c}`" for c in SCAN_FIELDS) + ", `local_id`"
                marks = ", ".join(["%s"] * (len(SCAN_FIELDS) + 1))
                cur.execute(
                    f"INSERT INTO LOHASAUTO_SCAN ({cols}) VALUES ({marks})",
                    [summary.get(k) for k in SCAN_FIELDS] + [local_scan_id],
                )
                scan_id = cur.lastrowid

                cids = cell_ids or [None] * len(cells)
                iids = item_ids or [None] * len(items)

                if cells:
                    ccols = ", ".join(
                        f"`{c}`" for c in ("scan_id",) + CELL_FIELDS
                        + ("created_at", "local_id"))
                    cmarks = ", ".join(["%s"] * (len(CELL_FIELDS) + 3))
                    cur.executemany(
                        f"INSERT INTO LOHASAUTO_SCAN_CELL ({ccols}) VALUES ({cmarks})",
                        [[scan_id] + [c.get(k) for k in CELL_FIELDS] + [ts, lid]
                         for c, lid in zip(cells, cids)],
                    )

                if items:
                    icols = ", ".join(
                        f"`{c}`" for c in ("scan_id",) + ITEM_FIELDS
                        + ("created_at", "local_id"))
                    imarks = ", ".join(["%s"] * (len(ITEM_FIELDS) + 3))
                    cur.executemany(
                        f"INSERT INTO LOHASAUTO_SCAN_ITEM ({icols}) VALUES ({imarks})",
                        [[scan_id] + [it.get(k) for k in ITEM_FIELDS] + [ts, lid]
                         for it, lid in zip(items, iids)],
                    )
        return f"MySQL 미러 저장 (scan_id={scan_id}, 셀 {len(cells)} / 상세 {len(items)})"
    except Exception as e:
        return f"MySQL 미러 실패: {e}"


def list_scans(folder_name: Optional[str] = None, limit: int = 50) -> list:
    sql = "SELECT * FROM scan"
    args = []
    if folder_name:
        sql += " WHERE folder_name = ?"
        args.append(folder_name)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with sqlite_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def list_scan_cells(scan_id: int) -> list:
    with sqlite_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM scan_cell WHERE scan_id = ? ORDER BY id", (scan_id,)
        ).fetchall()]


def list_scan_items(scan_id: int, target_only: bool = False) -> list:
    sql = "SELECT * FROM scan_item WHERE scan_id = ?"
    if target_only:
        sql += " AND bucket = 'target'"
    sql += " ORDER BY id"
    with sqlite_conn() as conn:
        return [dict(r) for r in conn.execute(sql, (scan_id,)).fetchall()]


def latest_scan(folder_name: str) -> Optional[dict]:
    rows = list_scans(folder_name, limit=1)
    return rows[0] if rows else None


# ---------------------------------------------------------------- 상품분석 이력

ANALYSIS_FIELDS = (
    "lcp_code", "folder_name", "product_no", "product_id",
    "analysis_no", "status", "state_msg", "analyzed_at",
)

# 로컬 파일 기록 (DB와 별개로 남기는 안전장치)
def analysis_log_path():
    return config.SQLITE_PATH.parent / "ss_analysis_done.jsonl"


def done_lcp_set(folder_name: str = None) -> set:
    """이미 상품분석이 끝난 LCP 집합 (재실행 방지용)."""
    sql = "SELECT lcp_code FROM ss_analysis WHERE status = 'done'"
    args = []
    if folder_name:
        sql += " AND folder_name = ?"
        args.append(folder_name)
    with sqlite_conn() as conn:
        return {r["lcp_code"] for r in conn.execute(sql, args).fetchall()}


def save_analysis(rec: dict) -> None:
    """상품분석 1건 기록/갱신 (SQLite + 로컬파일 + MySQL 미러)."""
    ts = now_str()
    rec = dict(rec)
    rec.setdefault("analyzed_at", None)

    cols = ", ".join(ANALYSIS_FIELDS)
    marks = ", ".join("?" * len(ANALYSIS_FIELDS))
    updates = ", ".join(f"{c} = excluded.{c}" for c in ANALYSIS_FIELDS[1:])
    with sqlite_conn() as conn:
        conn.execute(
            f"INSERT INTO ss_analysis ({cols}, created_at, updated_at) "
            f"VALUES ({marks}, ?, ?) "
            f"ON CONFLICT(lcp_code) DO UPDATE SET {updates}, updated_at = excluded.updated_at",
            [rec.get(k) for k in ANALYSIS_FIELDS] + [ts, ts],
        )

    if rec.get("status") == "done":
        try:
            import json as _json
            path = analysis_log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(_json.dumps({**rec, "recorded_at": ts},
                                    ensure_ascii=False) + chr(10))
        except Exception:
            pass

    conn2 = mysql_conn()
    if conn2 is not None:
        try:
            with conn2:
                with conn2.cursor() as cur:
                    for ddl in MYSQL_DDL:
                        cur.execute(ddl)
                    mcols = ", ".join(f"`{c}`" for c in ANALYSIS_FIELDS)
                    mmarks = ", ".join(["%s"] * len(ANALYSIS_FIELDS))
                    mupd = ", ".join(f"`{c}` = VALUES(`{c}`)"
                                     for c in ANALYSIS_FIELDS[1:])
                    cur.execute(
                        f"INSERT INTO LOHASAUTO_SS_ANALYSIS "
                        f"({mcols}, `created_at`, `updated_at`) "
                        f"VALUES ({mmarks}, %s, %s) "
                        f"ON DUPLICATE KEY UPDATE {mupd}, `updated_at` = VALUES(`updated_at`)",
                        [rec.get(k) for k in ANALYSIS_FIELDS] + [ts, ts],
                    )
        except Exception:
            pass


def analysis_stats(folder_name: str = None) -> dict:
    sql = "SELECT status, COUNT(*) n FROM ss_analysis"
    args = []
    if folder_name:
        sql += " WHERE folder_name = ?"
        args.append(folder_name)
    sql += " GROUP BY status"
    with sqlite_conn() as conn:
        return {r["status"]: r["n"] for r in conn.execute(sql, args).fetchall()}


# ---------------------------------------------------------------- 미분석 LCP 대기열

def save_queue(folder_name: str, items: list) -> int:
    """
    모니터링이 찾은 미분석 LCP 목록을 대기열에 반영한다.
    items: [{lcp_code, l_code, product_no}, ...]
    해당 폴더의 기존 대기열 중 목록에 없는 건은 지운다(이미 처리된 것).
    """
    ts = now_str()
    codes = [it["lcp_code"] for it in items if it.get("lcp_code")]
    with sqlite_conn() as conn:
        for it in items:
            if not it.get("lcp_code"):
                continue
            conn.execute(
                """
                INSERT INTO analysis_queue
                    (lcp_code, folder_name, l_code, product_no, found_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(lcp_code) DO UPDATE SET
                    folder_name = excluded.folder_name,
                    l_code      = excluded.l_code,
                    product_no  = excluded.product_no,
                    updated_at  = excluded.updated_at
                """,
                (it["lcp_code"], folder_name, it.get("l_code"),
                 it.get("product_no"), ts, ts),
            )
        if codes:
            marks = ",".join("?" * len(codes))
            conn.execute(
                f"DELETE FROM analysis_queue WHERE folder_name = ? "
                f"AND lcp_code NOT IN ({marks})",
                [folder_name] + codes)
        else:
            conn.execute("DELETE FROM analysis_queue WHERE folder_name = ?",
                         (folder_name,))
    return len(codes)


def list_queue(folder_name: str = None) -> list:
    sql = "SELECT * FROM analysis_queue"
    args = []
    if folder_name:
        sql += " WHERE folder_name = ?"
        args.append(folder_name)
    sql += " ORDER BY lcp_code"
    with sqlite_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def remove_from_queue(lcp_code: str) -> None:
    with sqlite_conn() as conn:
        conn.execute("DELETE FROM analysis_queue WHERE lcp_code = ?", (lcp_code,))


# ---------------------------------------------------------------- 작업 변동 로그

WORK_LOG_FIELDS = (
    "ts", "folder_name", "total_rows", "total_lcps",
    "img_done_rows", "img_work_rows", "info_save_rows", "info_todo_rows",
    "target_lcps", "analyzed_lcps", "pending_lcps",
    "d_img_done", "d_info_save", "d_info_todo", "d_analyzed", "d_pending",
    "elapsed_sec",
)


def work_log_path():
    return config.SQLITE_PATH.parent / "work_log.jsonl"


def save_work_log(rec: dict) -> None:
    """자동점검 1회분 기록 (SQLite + 로컬파일 + MySQL 미러)."""
    rec = dict(rec)
    rec.setdefault("ts", now_str())

    cols = ", ".join(WORK_LOG_FIELDS)
    marks = ", ".join("?" * len(WORK_LOG_FIELDS))
    with sqlite_conn() as conn:
        cur = conn.execute(f"INSERT INTO work_log ({cols}) VALUES ({marks})",
                           [rec.get(k) for k in WORK_LOG_FIELDS])
        local_id = cur.lastrowid

    try:
        import json as _json
        path = work_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + chr(10))
    except Exception:
        pass

    conn2 = mysql_conn()
    if conn2 is not None:
        try:
            with conn2:
                with conn2.cursor() as cur:
                    for ddl in MYSQL_DDL:
                        cur.execute(ddl)
                    mcols = (", ".join(f"`{c}`" for c in WORK_LOG_FIELDS)
                             + ", `local_id`")
                    mmarks = ", ".join(["%s"] * (len(WORK_LOG_FIELDS) + 1))
                    cur.execute(
                        f"INSERT INTO LOHASAUTO_WORK_LOG ({mcols}) VALUES ({mmarks})",
                        [rec.get(k) for k in WORK_LOG_FIELDS] + [local_id])
        except Exception:
            pass


def recent_work_log(folder_name: str = None, limit: int = 300) -> list:
    sql = "SELECT * FROM work_log"
    args = []
    if folder_name:
        sql += " WHERE folder_name = ?"
        args.append(folder_name)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with sqlite_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    return list(reversed(rows))


def hourly_stats(folder_name: str = None, hours: int = 24) -> list:
    """
    시간대별 처리량. 증가분(d_*)의 양수만 더해 '그 시간에 완료된 수'를 낸다.
    반환: [{hour, info_save, img_done, analyzed, samples}, ...] 오래된 순
    """
    sql = """
        SELECT substr(ts, 1, 13) AS hour,
               SUM(CASE WHEN d_info_save > 0 THEN d_info_save ELSE 0 END) AS info_save,
               SUM(CASE WHEN d_img_done  > 0 THEN d_img_done  ELSE 0 END) AS img_done,
               SUM(CASE WHEN d_analyzed  > 0 THEN d_analyzed  ELSE 0 END) AS analyzed,
               COUNT(*) AS samples
        FROM work_log
    """
    args = []
    if folder_name:
        sql += " WHERE folder_name = ?"
        args.append(folder_name)
    sql += " GROUP BY hour ORDER BY hour DESC LIMIT ?"
    args.append(hours)
    with sqlite_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    return list(reversed(rows))


# ---------------------------------------------------------------- 처리 속도

RATE_LOG_FIELDS = (
    "ts", "folder_name",
    "m30_info", "m30_img", "m30_analyzed",
    "h1_info", "h1_img", "h1_analyzed",
    "per10_info", "per10_img", "per10_analyzed",
    "pending_lcps", "eta_min",
)


def rate_log_path():
    return config.SQLITE_PATH.parent / "rate_log.jsonl"


def rate_stats(folder_name: str, minutes: int = 60,
               max_gap_min: float = 5.0) -> dict:
    """
    최근 N분간 처리량과 '실제 관측 시간'.

    자동점검을 껐다 켜면 그 공백까지 구간에 넣어버려 처리속도가 왜곡된다.
    그래서 연속된 두 표본의 간격이 max_gap_min 을 넘으면 그 구간은
    관측하지 않은 시간으로 보고 제외한다.

    반환: {'info', 'img', 'analyzed', 'span_min'(실관측), 'samples', 'gaps'}
    """
    from datetime import datetime, timedelta
    since = (datetime.now() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")

    sql = ("SELECT ts, d_info_save, d_img_done, d_analyzed FROM work_log "
           "WHERE ts >= ?")
    args = [since]
    if folder_name:
        sql += " AND folder_name = ?"
        args.append(folder_name)
    sql += " ORDER BY ts"
    with sqlite_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]

    info = img = analyzed = 0
    span_sec = 0.0
    gaps = 0
    prev_dt = None
    for r in rows:
        try:
            dt = datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        if prev_dt is not None:
            gap = (dt - prev_dt).total_seconds()
            if 0 < gap <= max_gap_min * 60:
                span_sec += gap
                # 관측이 이어진 구간의 증가분만 처리량으로 인정한다
                info += max(r["d_info_save"] or 0, 0)
                img += max(r["d_img_done"] or 0, 0)
                analyzed += max(r["d_analyzed"] or 0, 0)
            else:
                gaps += 1
        prev_dt = dt

    return {"info": info, "img": img, "analyzed": analyzed,
            "span_min": round(span_sec / 60.0, 2),
            "samples": len(rows), "gaps": gaps}


def per10_minutes(count: int, span_min: float):
    """count 건을 span_min 분에 처리했을 때, 10건당 걸리는 분. 0건이면 None."""
    if not count or count <= 0 or span_min <= 0:
        return None
    return round(span_min / count * 10.0, 1)


def save_rate_log(rec: dict) -> None:
    """처리속도 로그 1건 (SQLite + 로컬파일 + MySQL 미러)."""
    rec = dict(rec)
    rec.setdefault("ts", now_str())

    cols = ", ".join(RATE_LOG_FIELDS)
    marks = ", ".join("?" * len(RATE_LOG_FIELDS))
    with sqlite_conn() as conn:
        cur = conn.execute(f"INSERT INTO rate_log ({cols}) VALUES ({marks})",
                           [rec.get(k) for k in RATE_LOG_FIELDS])
        local_id = cur.lastrowid
    try:
        import json as _json
        path = rate_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + chr(10))
    except Exception:
        pass

    conn2 = mysql_conn()
    if conn2 is not None:
        try:
            with conn2:
                with conn2.cursor() as cur:
                    for ddl in MYSQL_DDL:
                        cur.execute(ddl)
                    mcols = (", ".join(f"`{c}`" for c in RATE_LOG_FIELDS)
                             + ", `local_id`")
                    mmarks = ", ".join(["%s"] * (len(RATE_LOG_FIELDS) + 1))
                    cur.execute(
                        f"INSERT INTO LOHASAUTO_RATE_LOG ({mcols}) VALUES ({mmarks})",
                        [rec.get(k) for k in RATE_LOG_FIELDS] + [local_id])
        except Exception:
            pass


def recent_rate_log(folder_name: str = None, limit: int = 100) -> list:
    sql = "SELECT * FROM rate_log"
    args = []
    if folder_name:
        sql += " WHERE folder_name = ?"
        args.append(folder_name)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with sqlite_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


# ---------------------------------------------------------------- 일/시간 집계

def _sum_sql(where_extra: str = "") -> str:
    return f"""
        SELECT SUM(CASE WHEN d_info_save > 0 THEN d_info_save ELSE 0 END) AS info,
               SUM(CASE WHEN d_img_done  > 0 THEN d_img_done  ELSE 0 END) AS img,
               SUM(CASE WHEN d_analyzed  > 0 THEN d_analyzed  ELSE 0 END) AS analyzed,
               COUNT(*) AS samples
        FROM work_log WHERE 1=1 {where_extra}
    """


def today_hourly(folder_name: str = None, date_str: str = None) -> list:
    """
    오늘(또는 지정일) 00~23시 시간대별 작업량. 데이터가 없는 시간도 0으로 채운다.
    반환: [{hour:0..23, info, img, analyzed, samples}, ...]
    """
    from datetime import datetime
    day = date_str or datetime.now().strftime("%Y-%m-%d")

    sql = """
        SELECT CAST(substr(ts, 12, 2) AS INTEGER) AS h,
               SUM(CASE WHEN d_info_save > 0 THEN d_info_save ELSE 0 END) AS info,
               SUM(CASE WHEN d_img_done  > 0 THEN d_img_done  ELSE 0 END) AS img,
               SUM(CASE WHEN d_analyzed  > 0 THEN d_analyzed  ELSE 0 END) AS analyzed,
               COUNT(*) AS samples
        FROM work_log WHERE substr(ts, 1, 10) = ?
    """
    args = [day]
    if folder_name:
        sql += " AND folder_name = ?"
        args.append(folder_name)
    sql += " GROUP BY h"

    with sqlite_conn() as conn:
        got = {r["h"]: dict(r) for r in conn.execute(sql, args).fetchall()}

    out = []
    for h in range(24):
        r = got.get(h)
        out.append({"hour": h,
                    "info": (r or {}).get("info") or 0,
                    "img": (r or {}).get("img") or 0,
                    "analyzed": (r or {}).get("analyzed") or 0,
                    "samples": (r or {}).get("samples") or 0})
    return out


def today_totals(folder_name: str = None, date_str: str = None) -> dict:
    """오늘 누적 작업량 + 관측 시간."""
    from datetime import datetime
    day = date_str or datetime.now().strftime("%Y-%m-%d")

    rows = today_hourly(folder_name, day)
    info = sum(r["info"] for r in rows)
    img = sum(r["img"] for r in rows)
    analyzed = sum(r["analyzed"] for r in rows)
    samples = sum(r["samples"] for r in rows)
    active_hours = len([r for r in rows if r["samples"] > 0])

    # 실제 관측한 시간(분) - 중단구간 제외
    span = rate_stats(folder_name, minutes=24 * 60)["span_min"]

    # **점검 기록으로도 재본다.** work_log 는 자동점검이 그 폴더를 볼 때만
    # 쌓인다. 하루 중간에 자동점검을 그 폴더로 옮기면 그 전에 한 일이 통째로
    # 안 보인다 — 엑사에서 오늘 2건(10->12)을 했는데 0 으로 나왔다
    # (2026-09-10 사용자). 어제 마지막 점검과 오늘 마지막 점검의 차이가
    # 그 폴더의 오늘 작업량이다.
    def _by_scan(col):
        sql = ("SELECT scanned_at, %s AS v FROM scan "
               "WHERE mode <> 'quick'" % col)
        a = []
        if folder_name:
            sql += " AND folder_name = ?"
            a.append(folder_name)
        sql += " ORDER BY scanned_at"
        with sqlite_conn() as conn:
            rows = [dict(r) for r in conn.execute(sql, a)]
        today = [r["v"] for r in rows if str(r["scanned_at"])[:10] == day
                 and r["v"] is not None]
        prev = [r for r in rows if str(r["scanned_at"])[:10] < day
                and r["v"] is not None]
        if not today or not prev:
            return 0
        # **어제 잰 것이어야 오늘 것으로 셀 수 있다.** 점검이 며칠 비었으면
        # 그 사이 늘어난 것을 오늘로 몰아 세게 된다 — 엑사에서 09-08 이후
        # 처음 재는 바람에 이미지승인 946건이 오늘로 잡혔다(2026-09-10).
        from datetime import datetime, timedelta
        last_day = str(prev[-1]["scanned_at"])[:10]
        y = (datetime.strptime(day, "%Y-%m-%d")
             - timedelta(days=1)).strftime("%Y-%m-%d")
        if last_day != y:
            return 0
        return max(0, today[-1] - prev[-1]["v"])

    info = max(info, _by_scan("info_save_rows"))
    img = max(img, _by_scan("img_done_rows"))
    return {"date": day, "info": info, "img": img, "analyzed": analyzed,
            "samples": samples, "active_hours": active_hours,
            "span_min": span,
            "per10_info": per10_minutes(info, span)}


def daily_stats(folder_name: str = None, days: int = 7) -> list:
    """최근 N일 일별 작업량 (오래된 순)."""
    sql = """
        SELECT substr(ts, 1, 10) AS day,
               SUM(CASE WHEN d_info_save > 0 THEN d_info_save ELSE 0 END) AS info,
               SUM(CASE WHEN d_img_done  > 0 THEN d_img_done  ELSE 0 END) AS img,
               SUM(CASE WHEN d_analyzed  > 0 THEN d_analyzed  ELSE 0 END) AS analyzed,
               COUNT(*) AS samples
        FROM work_log WHERE 1=1
    """
    args = []
    if folder_name:
        sql += " AND folder_name = ?"
        args.append(folder_name)
    sql += " GROUP BY day ORDER BY day DESC LIMIT ?"
    args.append(days)
    with sqlite_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    return list(reversed(rows))


# ---------------------------------------------------------------- 폴더별 일자 통계

def folder_daily(folder_name: str, days: int = 90) -> list:
    """
    폴더의 일자별 작업량 + 그날 마지막 스냅샷.

    반환(오래된 순): [{day, img_delta, info_delta, analyzed_delta,
                      total_rows, img_done_rows, info_save_rows,
                      info_todo_rows, samples}]
      *_delta   : 그날 실제로 늘어난 수량 (작업량)
      *_rows    : 그날 마지막 관측값 (그 시점의 총 수량)
    """
    sql = """
        SELECT d.day, d.img_delta, d.info_delta, d.analyzed_delta, d.samples,
               w.total_rows, w.img_done_rows, w.info_save_rows, w.info_todo_rows
        FROM (
            SELECT substr(ts, 1, 10) AS day,
                   SUM(CASE WHEN d_img_done  > 0 THEN d_img_done  ELSE 0 END) AS img_delta,
                   SUM(CASE WHEN d_info_save > 0 THEN d_info_save ELSE 0 END) AS info_delta,
                   SUM(CASE WHEN d_analyzed  > 0 THEN d_analyzed  ELSE 0 END) AS analyzed_delta,
                   COUNT(*) AS samples,
                   MAX(ts || '#' || printf('%012d', id)) AS last_key
            FROM work_log
            WHERE folder_name = ?
            GROUP BY day
        ) d
        JOIN work_log w
          ON w.ts || '#' || printf('%012d', w.id) = d.last_key
        ORDER BY d.day DESC LIMIT ?
    """
    with sqlite_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, (folder_name, days)).fetchall()]
    return list(reversed(rows))


def folder_overview(folder_name: str) -> dict:
    """
    폴더 한 줄 요약 : 최신 스냅샷 + 오늘 작업량 + 통계 시작일.
    데이터가 없으면 None 값들이 담긴 dict.
    """
    with sqlite_conn() as conn:
        last = conn.execute(
            "SELECT * FROM work_log WHERE folder_name = ? "
            "ORDER BY ts DESC, id DESC LIMIT 1", (folder_name,)).fetchone()
        first = conn.execute(
            "SELECT MIN(substr(ts,1,10)) AS d, COUNT(*) AS n "
            "FROM work_log WHERE folder_name = ?", (folder_name,)).fetchone()

    today = today_totals(folder_name)
    return {
        "has_data": last is not None,
        "since": (first["d"] if first else None),
        "samples": (first["n"] if first else 0),
        "total_rows": last["total_rows"] if last else None,
        "img_done_rows": last["img_done_rows"] if last else None,
        "info_save_rows": last["info_save_rows"] if last else None,
        "info_todo_rows": last["info_todo_rows"] if last else None,
        "pending_lcps": last["pending_lcps"] if last else None,
        "last_ts": last["ts"] if last else None,
        "today_img": today["img"],
        "today_info": today["info"],
        "today_analyzed": today["analyzed"],
    }


# ---------------------------------------------------------------- 증분 보정

def last_work_log(folder_name: str):
    """폴더의 마지막 work_log 1건 (모니터 재시작 시 기준점으로 쓴다)."""
    with sqlite_conn() as conn:
        r = conn.execute(
            "SELECT * FROM work_log WHERE folder_name = ? "
            "ORDER BY ts DESC, id DESC LIMIT 1", (folder_name,)).fetchone()
    return dict(r) if r else None


def recompute_deltas(folder_name: str = None) -> dict:
    """
    저장된 스냅샷으로 증분(d_*)을 다시 계산한다.

    자동점검을 껐다 켜면 첫 주기의 증분이 0으로 기록돼 그 공백 동안의
    작업량이 통째로 누락된다. 스냅샷 값은 남아 있으므로 연속된 두 행의
    차이로 다시 채워 넣으면 일별 합계가 실제와 맞는다.

    (구간이 벌어진 증분은 rate_stats 가 시간간격으로 걸러내므로
     처리속도 계산은 왜곡되지 않는다)
    """
    sql = "SELECT * FROM work_log"
    args = []
    if folder_name:
        sql += " WHERE folder_name = ?"
        args.append(folder_name)
    # 나중에 끼워넣은 기준행도 제자리에 놓이도록 시간순으로 본다
    sql += " ORDER BY folder_name, ts, id"

    fixed = 0
    with sqlite_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        prev = {}
        for r in rows:
            f = r["folder_name"]
            p = prev.get(f)
            if p is None:
                d = {"d_img_done": 0, "d_info_save": 0,
                     "d_info_todo": 0, "d_analyzed": 0, "d_pending": 0}
            else:
                d = {
                    "d_img_done": (r["img_done_rows"] or 0) - (p["img_done_rows"] or 0),
                    "d_info_save": (r["info_save_rows"] or 0) - (p["info_save_rows"] or 0),
                    "d_info_todo": (r["info_todo_rows"] or 0) - (p["info_todo_rows"] or 0),
                    "d_analyzed": (r["analyzed_lcps"] or 0) - (p["analyzed_lcps"] or 0),
                    "d_pending": (r["pending_lcps"] or 0) - (p["pending_lcps"] or 0),
                }
            if any(r.get(k) != v for k, v in d.items()):
                conn.execute(
                    "UPDATE work_log SET d_img_done=?, d_info_save=?, "
                    "d_info_todo=?, d_analyzed=?, d_pending=? WHERE id=?",
                    (d["d_img_done"], d["d_info_save"], d["d_info_todo"],
                     d["d_analyzed"], d["d_pending"], r["id"]))
                fixed += 1
            prev[f] = r
    return {"rows": len(rows), "fixed": fixed}


# ---------------------------------------------------------------- 작업 로그

TASK_LOG_FIELDS = (
    "ts", "folder_name", "lcp_code", "l_code", "product_no",
    "step", "action", "status", "picked", "candidates", "picked_count",
    "source", "message", "elapsed_sec",
)


def task_log_path():
    return config.SQLITE_PATH.parent / "task_log.jsonl"


def save_task_log(rec: dict) -> int:
    """상품 편집 작업 1단계 기록 (SQLite + 로컬파일 + MySQL 미러)."""
    rec = dict(rec)
    rec.setdefault("ts", now_str())
    if isinstance(rec.get("picked"), (list, tuple)):
        rec["picked_count"] = len(rec["picked"])
        rec["picked"] = ", ".join(rec["picked"])

    cols = ", ".join(TASK_LOG_FIELDS)
    marks = ", ".join("?" * len(TASK_LOG_FIELDS))
    with sqlite_conn() as conn:
        cur = conn.execute(f"INSERT INTO task_log ({cols}) VALUES ({marks})",
                           [rec.get(k) for k in TASK_LOG_FIELDS])
        local_id = cur.lastrowid

    try:
        import json as _json
        path = task_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + chr(10))
    except Exception:
        pass

    conn2 = mysql_conn()
    if conn2 is not None:
        try:
            with conn2:
                mysql_prepare(conn2)
                with conn2.cursor() as cur:
                    mcols = (", ".join(f"`{c}`" for c in TASK_LOG_FIELDS)
                             + ", `local_id`")
                    mmarks = ", ".join(["%s"] * (len(TASK_LOG_FIELDS) + 1))
                    cur.execute(
                        f"INSERT INTO LOHASAUTO_TASK_LOG ({mcols}) VALUES ({mmarks})",
                        [rec.get(k) for k in TASK_LOG_FIELDS] + [local_id])
        except Exception:
            pass
    return local_id


def recent_task_log(folder_name: str = None, limit: int = 200) -> list:
    sql = "SELECT * FROM task_log"
    args = []
    if folder_name:
        sql += " WHERE folder_name = ?"
        args.append(folder_name)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with sqlite_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


# ---------------------------------------------------------------- LCP 수집 저장

def lcp_collect_path():
    return config.SQLITE_PATH.parent / "lcp_collect.jsonl"


def save_lcp_collect(rec: dict, folder_name: str = None) -> dict:
    """
    collect.collect_one() 결과를 저장. 같은 LCP 는 덮어쓴다.
    로컬 SQLite + JSONL + 서버 MySQL(있으면).
    """
    import json as _json
    ts = now_str()
    lcp = rec["lcp_code"]
    tie = rec.get("tie") or {}
    kw = rec.get("keywords") or {}
    cats = rec.get("categories") or []

    opts = tie.get("options") or []
    toks = rec.get("title_tokens") or []
    used = kw.get("used") or []
    recs = kw.get("recommend") or []
    wish = tie.get("wish_keywords") or []

    tie = dict(tie, _pid=rec.get("product_id"), _no=rec.get("product_no"),
               _used=len(used), _rec=len(recs))

    with sqlite_conn() as conn:
        conn.execute(
            """
            INSERT INTO lcp_product
                (lcp_code, folder_name, product_id, product_no, product_name,
                 brand, maker, origin, cost, markets, wish_keywords,
                 option_count, used_count, rec_count, cat_count, token_count,
                 collected_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(lcp_code) DO UPDATE SET
                folder_name=excluded.folder_name, product_id=excluded.product_id,
                product_no=excluded.product_no, product_name=excluded.product_name,
                brand=excluded.brand, maker=excluded.maker, origin=excluded.origin,
                cost=excluded.cost, markets=excluded.markets,
                wish_keywords=excluded.wish_keywords,
                option_count=excluded.option_count, used_count=excluded.used_count,
                rec_count=excluded.rec_count, cat_count=excluded.cat_count,
                token_count=excluded.token_count,
                updated_at=excluded.updated_at
            """,
            (lcp, folder_name, rec.get("product_id"), rec.get("product_no"),
             tie.get("product_name"), tie.get("brand"), tie.get("maker"),
             tie.get("origin"), tie.get("cost"),
             _json.dumps(tie.get("markets") or {}, ensure_ascii=False),
             " ".join(wish), len(opts), len(used), len(recs), len(cats),
             len(toks), ts, ts))

        for t in ("lcp_option", "lcp_keyword", "lcp_category"):
            conn.execute(f"DELETE FROM {t} WHERE lcp_code = ?", (lcp,))

        conn.executemany(
            "INSERT INTO lcp_option (lcp_code, seq, name, subs, created_at) "
            "VALUES (?,?,?,?,?)",
            [(lcp, o.get("seq"), o.get("name"),
              _json.dumps(o.get("subs") or [], ensure_ascii=False), ts)
             for o in opts])

        rows = []
        for u in used:
            rows.append((lcp, u["keyword"], "used", u.get("views"),
                         None, None, None, ts))
        for r in recs:
            rows.append((lcp, r["keyword"], "recommend", None,
                         r.get("auction"), r.get("gmarket"), r.get("total"), ts))
        for w in wish:
            rows.append((lcp, w, "wish", None, None, None, None, ts))
        # 옵션 제품명을 띄어쓰기 단위로 쪼갠 상품명 키워드 (total=등장횟수)
        for t in toks:
            rows.append((lcp, t["token"], "token", None,
                         None, None, t.get("freq"), ts))
        conn.executemany(
            "INSERT INTO lcp_keyword (lcp_code, keyword, source, views, "
            "auction, gmarket, total, created_at) VALUES (?,?,?,?,?,?,?,?)", rows)

        conn.executemany(
            "INSERT INTO lcp_category (lcp_code, code, name, cnt, unit, "
            "capacity, rank, created_at) VALUES (?,?,?,?,?,?,?,?)",
            [(lcp, c.get("code"), c.get("name"), c.get("cnt"),
              c.get("unit"), c.get("capacity"), i + 1, ts)
             for i, c in enumerate(cats)])

    try:
        path = lcp_collect_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(_json.dumps({**rec, "folder_name": folder_name,
                                 "saved_at": ts}, ensure_ascii=False) + chr(10))
    except Exception:
        pass

    mirror = _mirror_lcp_collect(lcp, folder_name, tie, opts, rows, cats, toks, ts)

    return {"lcp_code": lcp, "options": len(opts), "used": len(used),
            "recommend": len(recs), "wish": len(wish), "tokens": len(toks),
            "categories": len(cats),
            "keywords_total": len(used) + len(recs) + len(wish) + len(toks),
            "mirror": mirror}


def _mirror_lcp_collect(lcp, folder_name, tie, opts, kw_rows, cats, toks, ts) -> str:
    """LCP 수집 결과를 서버 MySQL 로 미러링. 같은 LCP 는 지우고 다시 넣는다."""
    import json as _json

    conn = mysql_conn()
    if conn is None:
        return "" if not config.MYSQL_ENABLED else "MySQL 미러 실패(연결)"
    try:
        with conn:
            mysql_prepare(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO LOHASAUTO_LCP_PRODUCT "
                    "(`lcp_code`,`folder_name`,`product_id`,`product_no`,"
                    "`product_name`,`brand`,`maker`,`origin`,`cost`,`markets`,"
                    "`wish_keywords`,`option_count`,`used_count`,`rec_count`,"
                    "`cat_count`,`token_count`,`collected_at`,`updated_at`) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE "
                    "folder_name=VALUES(folder_name), product_id=VALUES(product_id),"
                    "product_no=VALUES(product_no), product_name=VALUES(product_name),"
                    "brand=VALUES(brand), maker=VALUES(maker), origin=VALUES(origin),"
                    "cost=VALUES(cost), markets=VALUES(markets),"
                    "wish_keywords=VALUES(wish_keywords),"
                    "option_count=VALUES(option_count), used_count=VALUES(used_count),"
                    "rec_count=VALUES(rec_count), cat_count=VALUES(cat_count),"
                    "token_count=VALUES(token_count), updated_at=VALUES(updated_at)",
                    (lcp, folder_name, tie.get("_pid"), tie.get("_no"),
                     tie.get("product_name"), tie.get("brand"), tie.get("maker"),
                     tie.get("origin"), tie.get("cost"),
                     _json.dumps(tie.get("markets") or {}, ensure_ascii=False),
                     " ".join(tie.get("wish_keywords") or []),
                     len(opts), tie.get("_used", 0), tie.get("_rec", 0),
                     len(cats), len(toks), ts, ts))

                for t in ("LOHASAUTO_LCP_OPTION", "LOHASAUTO_LCP_KEYWORD",
                          "LOHASAUTO_LCP_CATEGORY"):
                    cur.execute(f"DELETE FROM {t} WHERE lcp_code = %s", (lcp,))

                if opts:
                    cur.executemany(
                        "INSERT INTO LOHASAUTO_LCP_OPTION "
                        "(`lcp_code`,`seq`,`name`,`subs`,`created_at`) "
                        "VALUES (%s,%s,%s,%s,%s)",
                        [(lcp, o.get("seq"), o.get("name"),
                          _json.dumps(o.get("subs") or [], ensure_ascii=False), ts)
                         for o in opts])
                if kw_rows:
                    cur.executemany(
                        "INSERT INTO LOHASAUTO_LCP_KEYWORD "
                        "(`lcp_code`,`keyword`,`source`,`views`,`auction`,"
                        "`gmarket`,`total`,`created_at`) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", kw_rows)
                if cats:
                    cur.executemany(
                        "INSERT INTO LOHASAUTO_LCP_CATEGORY "
                        "(`lcp_code`,`code`,`name`,`cnt`,`unit`,`capacity`,"
                        "`rank`,`created_at`) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        [(lcp, c.get("code"), c.get("name"), c.get("cnt"),
                          c.get("unit"), c.get("capacity"), i + 1, ts)
                         for i, c in enumerate(cats)])
        return f"MySQL 미러 (키워드 {len(kw_rows)} / 옵션 {len(opts)} / 카테고리 {len(cats)})"
    except Exception as e:
        return f"MySQL 미러 실패: {e}"


def lcp_collect_stats() -> dict:
    with sqlite_conn() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM lcp_product").fetchone()["c"]
        k = conn.execute("SELECT COUNT(*) c FROM lcp_keyword").fetchone()["c"]
        o = conn.execute("SELECT COUNT(*) c FROM lcp_option").fetchone()["c"]
        c2 = conn.execute("SELECT COUNT(*) c FROM lcp_category").fetchone()["c"]
    return {"lcp": n, "keywords": k, "options": o, "categories": c2}


def collected_lcps() -> set:
    with sqlite_conn() as conn:
        return {r["lcp_code"] for r in
                conn.execute("SELECT lcp_code FROM lcp_product").fetchall()}


# ---------------------------------------------------------------- L코드 상태

def save_lcode_status(folder_name: str, rows: list) -> dict:
    """L코드 상태를 저장(UPSERT). 해당 폴더의 기존 행 중 없어진 것은 지운다."""
    ts = now_str()
    keys = {(r["lcp_code"], r["l_code"]) for r in rows}

    with sqlite_conn() as conn:
        conn.executemany(
            """
            INSERT INTO lcp_lcode
                (lcp_code, l_code, folder_name, product_no,
                 img_status, info_status, updated_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(lcp_code, l_code) DO UPDATE SET
                folder_name=excluded.folder_name,
                product_no=excluded.product_no,
                img_status=excluded.img_status,
                info_status=excluded.info_status,
                updated_at=excluded.updated_at
            """,
            [(r["lcp_code"], r["l_code"], folder_name, r.get("product_no"),
              r.get("img_status"), r.get("info_status"), ts) for r in rows])

        old = conn.execute(
            "SELECT lcp_code, l_code FROM lcp_lcode WHERE folder_name = ?",
            (folder_name,)).fetchall()
        gone = [(r["lcp_code"], r["l_code"]) for r in old
                if (r["lcp_code"], r["l_code"]) not in keys]
        if gone:
            conn.executemany(
                "DELETE FROM lcp_lcode WHERE lcp_code=? AND l_code=?", gone)

    mirror = ""
    conn2 = mysql_conn()
    if conn2 is not None:
        try:
            with conn2:
                mysql_prepare(conn2)
                with conn2.cursor() as cur:
                    cur.execute("DELETE FROM LOHASAUTO_LCP_LCODE "
                                "WHERE folder_name = %s", (folder_name,))
                    for i in range(0, len(rows), 500):
                        part = rows[i:i + 500]
                        cur.executemany(
                            "INSERT INTO LOHASAUTO_LCP_LCODE "
                            "(`lcp_code`,`l_code`,`folder_name`,`product_no`,"
                            "`img_status`,`info_status`,`updated_at`) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                            [(r["lcp_code"], r["l_code"], folder_name,
                              r.get("product_no"), r.get("img_status"),
                              r.get("info_status"), ts) for r in part])
            mirror = f"MySQL 미러 {len(rows):,}행"
        except Exception as e:
            mirror = f"MySQL 미러 실패: {e}"

    return {"rows": len(rows), "removed": len(gone), "mirror": mirror}


def lcode_rows(folder_name: str = None, lcp_code: str = None) -> list:
    sql = "SELECT * FROM lcp_lcode WHERE 1=1"
    args = []
    if folder_name:
        sql += " AND folder_name = ?"
        args.append(folder_name)
    if lcp_code:
        sql += " AND lcp_code = ?"
        args.append(lcp_code)
    sql += " ORDER BY lcp_code, l_code"
    with sqlite_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def lcp_overview(folder_name: str) -> list:
    """
    상품정보 화면의 한 줄 표시용. LCP 별 L코드 상태 요약 + 수집 정보를 합친다.
    """
    sql = """
        SELECT l.lcp_code,
               COUNT(*)                                            AS total,
               SUM(l.img_status = '이미지승인완료')                 AS img_done,
               SUM(l.img_status = '이미지작업')                     AS img_work,
               SUM(l.img_status = '미작업')                         AS img_todo,
               SUM(l.info_status = '저장완료')                      AS info_save,
               SUM(l.info_status = '미작업')                        AS info_todo,
               SUM(l.info_status = '제외')                          AS info_exclude,
               SUM(l.info_status = '보류')                          AS info_hold,
               SUM(l.img_status = '이미지승인완료'
                   AND l.info_status = '미작업')                    AS target,
               p.product_name, p.brand, p.cost,
               p.used_count, p.rec_count, p.token_count, p.cat_count,
               p.option_count, p.collected_at
        FROM lcp_lcode l
        LEFT JOIN lcp_product p ON p.lcp_code = l.lcp_code
        WHERE l.folder_name = ?
        GROUP BY l.lcp_code
        ORDER BY target DESC, l.lcp_code
    """
    with sqlite_conn() as conn:
        return [dict(r) for r in conn.execute(sql, (folder_name,)).fetchall()]


# ---------------------------------------------------------------- 카테고리 역조회

def category_list(folder_name: str = None, min_lcp: int = 1) -> list:
    """
    수집된 카테고리 목록 (역방향 조회용).
    반환: [{code, name, lcp_count, total_cnt, top_cnt}]
      lcp_count : 이 카테고리가 등장한 LCP 수
      total_cnt : 그 LCP 들에서 이 카테고리에 속한 L코드 합계
      top_cnt   : 어느 LCP 에서 1순위였던 횟수
    """
    sql = """
        SELECT c.code, c.name,
               COUNT(DISTINCT c.lcp_code)              AS lcp_count,
               SUM(c.cnt)                              AS total_cnt,
               SUM(CASE WHEN c.rank = 1 THEN 1 ELSE 0 END) AS top_cnt
        FROM lcp_category c
    """
    args = []
    if folder_name:
        sql += " JOIN lcp_product p ON p.lcp_code = c.lcp_code AND p.folder_name = ?"
        args.append(folder_name)
    sql += " GROUP BY c.code, c.name HAVING lcp_count >= ? ORDER BY total_cnt DESC"
    args.append(min_lcp)
    with sqlite_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def category_lcps(code: str) -> list:
    """이 카테고리에 걸린 LCP 목록 (해당 카테고리 L코드 수 내림차순)."""
    sql = """
        SELECT c.lcp_code, c.cnt, c.rank,
               p.product_name, p.brand, p.used_count, p.rec_count,
               p.token_count, p.cat_count
        FROM lcp_category c
        LEFT JOIN lcp_product p ON p.lcp_code = c.lcp_code
        WHERE c.code = ?
        ORDER BY c.cnt DESC, c.lcp_code
    """
    with sqlite_conn() as conn:
        return [dict(r) for r in conn.execute(sql, (code,)).fetchall()]


def category_keywords(code: str, source: str = None, limit: int = 500) -> list:
    """
    이 카테고리에 걸린 LCP 들의 키워드를 모아 집계.
    '이 카테고리 상품에는 실제로 이런 키워드가 붙는다' 는 사전이 된다.

    반환: [{keyword, source, lcp_count, uses, views}]
      lcp_count : 몇 개 LCP 에서 쓰였나 (많을수록 대표성 높음)
    """
    sql = """
        SELECT k.keyword, k.source,
               COUNT(DISTINCT k.lcp_code) AS lcp_count,
               COUNT(*)                   AS uses,
               MAX(COALESCE(k.views, k.total)) AS views
        FROM lcp_keyword k
        WHERE k.lcp_code IN (SELECT lcp_code FROM lcp_category WHERE code = ?)
    """
    args = [code]
    if source:
        sql += " AND k.source = ?"
        args.append(source)
    sql += (" GROUP BY k.keyword, k.source"
            " ORDER BY lcp_count DESC, uses DESC, k.keyword LIMIT ?")
    args.append(limit)
    with sqlite_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def category_stats() -> dict:
    with sqlite_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(DISTINCT code) c FROM lcp_category").fetchone()["c"]
        rows = conn.execute("SELECT COUNT(*) c FROM lcp_category").fetchone()["c"]
    return {"categories": n, "rows": rows}


# ---------------------------------------------------------------- 상품 작업 상세

ATTR_FIELDS = ("product_no", "lcp_code", "l_code", "folder_name",
               "etc_category", "analysis_date", "analysis_done",
               "cat_saved", "attr_saved", "title_saved", "title1",
               "title_count", "tag_count", "attribute_count",
               "titles", "tags", "next_step")


def save_lcode_attr(folder_name: str, rows: list) -> dict:
    """L코드별 작업 상세 저장 (UPSERT) + MySQL 미러."""
    import json as _json

    ts = now_str()
    vals = []
    for r in rows:
        vals.append((
            r.get("product_no"), r.get("lcp_code"), r.get("l_code"), folder_name,
            r.get("etc_category"), r.get("analysis_date"),
            int(bool(r.get("analysis_done"))), int(bool(r.get("cat_saved"))),
            int(bool(r.get("attr_saved"))), int(bool(r.get("title_saved"))),
            (r.get("title1") or "")[:500], r.get("title_count"),
            r.get("tag_count"), r.get("attribute_count"),
            _json.dumps(r.get("titles") or [], ensure_ascii=False),
            _json.dumps(r.get("tags") or [], ensure_ascii=False),
            r.get("next_step"), ts))

    cols = ", ".join(ATTR_FIELDS) + ", updated_at"
    marks = ", ".join("?" * (len(ATTR_FIELDS) + 1))
    upd = ", ".join(f"{c}=excluded.{c}" for c in ATTR_FIELDS[1:])
    with sqlite_conn() as conn:
        conn.executemany(
            f"INSERT INTO lcode_attr ({cols}) VALUES ({marks}) "
            f"ON CONFLICT(product_no) DO UPDATE SET {upd}, updated_at=excluded.updated_at",
            vals)

    mirror = ""
    conn2 = mysql_conn()
    if conn2 is not None:
        try:
            with conn2:
                mysql_prepare(conn2)
                with conn2.cursor() as cur:
                    mcols = ", ".join(f"`{c}`" for c in ATTR_FIELDS) + ", `updated_at`"
                    mmarks = ", ".join(["%s"] * (len(ATTR_FIELDS) + 1))
                    mupd = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in ATTR_FIELDS[1:])
                    for i in range(0, len(vals), 400):
                        cur.executemany(
                            f"INSERT INTO LOHASAUTO_LCODE_ATTR ({mcols}) "
                            f"VALUES ({mmarks}) ON DUPLICATE KEY UPDATE "
                            f"{mupd}, `updated_at`=VALUES(`updated_at`)",
                            vals[i:i + 400])
            mirror = f"MySQL 미러 {len(vals):,}행"
        except Exception as e:
            mirror = f"MySQL 미러 실패: {e}"
    return {"rows": len(vals), "mirror": mirror}


def attr_summary(folder_name: str = None) -> dict:
    """작업 단계별 상품 수."""
    sql = ("SELECT next_step, COUNT(*) n, SUM(cat_saved) cat, "
           "SUM(attr_saved) attr, SUM(title_saved) title FROM lcode_attr")
    args = []
    if folder_name:
        sql += " WHERE folder_name = ?"
        args.append(folder_name)
    sql += " GROUP BY next_step ORDER BY n DESC"
    with sqlite_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        tot = conn.execute(
            "SELECT COUNT(*) n, SUM(cat_saved) cat, SUM(attr_saved) attr, "
            "SUM(title_saved) title, SUM(analysis_done) ana FROM lcode_attr"
            + (" WHERE folder_name = ?" if folder_name else ""), args).fetchone()
    return {"steps": rows, "total": dict(tot) if tot else {}}


def lcode_attr_rows(lcp_code: str = None, folder_name: str = None) -> list:
    sql = "SELECT * FROM lcode_attr WHERE 1=1"
    args = []
    if lcp_code:
        sql += " AND lcp_code = ?"
        args.append(lcp_code)
    if folder_name:
        sql += " AND folder_name = ?"
        args.append(folder_name)
    sql += " ORDER BY lcp_code, l_code"
    with sqlite_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


# ------------------------------------------------- 네이버 데이터랩 인기키워드

def save_datalab_keywords(cid: str, ranks: list, cat_name: str = "",
                          days: int = 30) -> dict:
    """
    카테고리 하나의 인기키워드를 통째로 갈아끼운다.
    순위가 바뀌므로 누적하지 않고 cid 단위로 지우고 다시 넣는다.
    """
    ts = now_str()
    rows = []
    for r in ranks:
        kw = (r.get("keyword") or "").strip()
        if not kw:
            continue
        # views 는 enrich 로 채워 넣은 값이다. 없으면 NULL 로 둔다 - 0 으로
        # 채우면 '조회수 0' 과 '아직 안 잰 것' 을 구분할 수 없다.
        rows.append((str(cid), int(r.get("rank") or 0), kw, cat_name, days, ts,
                     r.get("views"), r.get("pc_views"), r.get("mobile_views"),
                     r.get("comp_idx"), r.get("product_count")))
    if not rows:
        return {"rows": 0, "mirror": ""}

    with sqlite_conn() as conn:
        conn.execute("DELETE FROM datalab_keyword WHERE cid = ?", (str(cid),))
        conn.executemany(
            "INSERT OR REPLACE INTO datalab_keyword "
            "(cid, rank, keyword, cat_name, days, collected_at, views, "
            " pc_views, mobile_views, comp_idx, product_count) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)

    path = config.SQLITE_PATH.parent / "datalab_keyword.jsonl"
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(
                {"ts": ts, "cid": str(cid), "cat_name": cat_name,
                 "days": days, "count": len(rows),
                 "keywords": [r[2] for r in rows]},
                ensure_ascii=False) + "\n")
    except Exception:
        pass

    return {"rows": len(rows), "mirror": _mirror_datalab(rows)}


def _mirror_datalab(rows: list) -> str:
    conn = mysql_conn()
    if conn is None:
        return "" if not config.MYSQL_ENABLED else "MySQL 미러 실패(연결)"
    try:
        with conn:
            mysql_prepare(conn)
            with conn.cursor() as cur:
                cur.execute("DELETE FROM LOHASAUTO_DATALAB_KEYWORD "
                            "WHERE cid = %s", (rows[0][0],))
                cur.executemany(
                    "INSERT INTO LOHASAUTO_DATALAB_KEYWORD "
                    "(`cid`,`rank`,`keyword`,`cat_name`,`days`,`collected_at`) "
                    "VALUES (%s,%s,%s,%s,%s,%s)", rows)
        return f"MySQL 미러 {len(rows):,}행"
    except Exception as e:
        return f"MySQL 미러 실패({str(e)[:60]})"


def datalab_keywords(cid: str, limit: int = 500) -> list:
    with sqlite_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM datalab_keyword WHERE cid = ? "
            "ORDER BY rank LIMIT ?", (str(cid), limit)).fetchall()]


def datalab_have() -> dict:
    """이미 수집한 카테고리 -> {건수, 수집시각}."""
    with sqlite_conn() as conn:
        return {r["cid"]: {"n": r["n"], "at": r["at"], "name": r["cat_name"]}
                for r in conn.execute(
                    "SELECT cid, COUNT(*) n, MAX(collected_at) at, "
                    "MAX(cat_name) cat_name FROM datalab_keyword "
                    "GROUP BY cid").fetchall()}


def todo_lcodes(folder_name: str = None) -> list:
    """
    대표이미지는 승인완료인데 상품정보가 미작업인 L코드.
    이미지가 끝났으니 바로 상품정보 작업에 들어갈 수 있는 것들이다.
    """
    sql = ("SELECT c.lcp_code, c.l_code, c.product_no, c.img_status, "
           "c.info_status, p.product_name, p.wish_keywords, p.used_count, "
           "p.rec_count, p.token_count, a.etc_category, a.next_step, "
           "a.analysis_done, a.cat_saved, a.attr_saved, a.title_saved, "
           "a.tag_count, a.title1 "
           "FROM lcp_lcode c "
           "LEFT JOIN lcp_product p ON p.lcp_code = c.lcp_code "
           "LEFT JOIN lcode_attr a ON a.product_no = c.product_no "
           "WHERE c.img_status = '이미지승인완료' AND c.info_status = '미작업'")
    args = []
    if folder_name:
        sql += " AND c.folder_name = ?"
        args.append(folder_name)
    sql += " ORDER BY c.lcp_code, c.l_code"
    with sqlite_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def lcp_keywords(lcp_code: str, source: str = None, limit: int = 800) -> list:
    sql = "SELECT * FROM lcp_keyword WHERE lcp_code = ?"
    args = [lcp_code]
    if source:
        sql += " AND source = ?"
        args.append(source)
    sql += " ORDER BY (views IS NULL), views DESC LIMIT ?"
    args.append(limit)
    with sqlite_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def lcp_categories(lcp_code: str) -> list:
    with sqlite_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM lcp_category WHERE lcp_code = ? "
            "ORDER BY rank, cnt DESC", (lcp_code,)).fetchall()]


# ----------------------------------------- 로하스 태그/상품명 탭 키워드 (카테고리별)

def save_cat_keywords(lcp_code: str, rows: list) -> dict:
    """한 LCP 의 태그·상품명 키워드를 통째로 갈아끼운다."""
    ts = now_str()
    vals = []
    for r in rows:
        kw = (r.get("keyword") or "").strip()
        if not kw:
            continue
        vals.append((
            str(r.get("cid") or ""), lcp_code, str(r.get("product_no") or ""),
            r.get("kind"), int(r.get("title_no") or 0), kw,
            int(r.get("views") or 0), r.get("banned") or "",
            r.get("used") or "", int(r.get("prio") or 0),
            1 if r.get("is_dict") else 0, 1 if r.get("is_rec") else 0, ts))
    with sqlite_conn() as conn:
        conn.execute("DELETE FROM cat_keyword WHERE lcp_code = ?", (lcp_code,))
        if vals:
            conn.executemany(
                "INSERT OR REPLACE INTO cat_keyword (cid, lcp_code, product_no,"
                " kind, title_no, keyword, views, banned, used, prio, is_dict,"
                " is_rec, collected_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                vals)
    return {"rows": len(vals), "mirror": _mirror_cat_keywords(lcp_code, vals)}


def _mirror_cat_keywords(lcp_code: str, vals: list) -> str:
    conn = mysql_conn()
    if conn is None:
        return "" if not config.MYSQL_ENABLED else "MySQL 미러 실패(연결)"
    try:
        with conn:
            mysql_prepare(conn)
            with conn.cursor() as cur:
                cur.execute("DELETE FROM LOHASAUTO_CAT_KEYWORD "
                            "WHERE lcp_code = %s", (lcp_code,))
                if vals:
                    cur.executemany(
                        "INSERT INTO LOHASAUTO_CAT_KEYWORD (`cid`,`lcp_code`,"
                        "`product_no`,`kind`,`title_no`,`keyword`,`views`,"
                        "`banned`,`used`,`prio`,`is_dict`,`is_rec`,"
                        "`collected_at`) VALUES "
                        "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", vals)
        return f"MySQL 미러 {len(vals):,}행"
    except Exception as e:
        return f"MySQL 미러 실패({str(e)[:60]})"


def cat_keyword_lcps() -> dict:
    """이미 긁은 LCP -> {태그수, 상품명수}."""
    with sqlite_conn() as conn:
        out = {}
        for r in conn.execute(
                "SELECT lcp_code, kind, COUNT(*) n FROM cat_keyword "
                "GROUP BY lcp_code, kind"):
            out.setdefault(r["lcp_code"], {})[r["kind"]] = r["n"]
        return out


def cat_keywords(cid: str = None, lcp_code: str = None, kind: str = None,
                 max_views: int = 0, usable_only: bool = False,
                 limit: int = 2000) -> list:
    """
    카테고리(cid) 또는 LCP 단위로 키워드를 뽑는다.
    같은 키워드가 여러 LCP 에 나오면 합쳐서 '몇 개 LCP 가 쓰는지'를 함께 준다.

    max_views   0 보다 크면 조회수가 그 미만인 것만 (로하스 지침 = 1000 미만)
    usable_only 금지어가 붙은 키워드를 뺀다
    """
    sql = ("SELECT keyword, kind, MAX(views) views, "
           "COUNT(DISTINCT lcp_code) lcp_count, MAX(banned) banned, "
           "MAX(prio) prio, MAX(is_dict) is_dict, MAX(is_rec) is_rec "
           "FROM cat_keyword WHERE 1=1")
    args = []
    if cid:
        sql += " AND cid = ?"
        args.append(str(cid))
    if lcp_code:
        sql += " AND lcp_code = ?"
        args.append(lcp_code)
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    if usable_only:
        sql += " AND (banned IS NULL OR banned = '')"
    if max_views:
        sql += " AND views < ? AND views > 0"
        args.append(int(max_views))
    sql += (" GROUP BY keyword, kind ORDER BY lcp_count DESC, prio DESC,"
            " views DESC LIMIT ?")
    args.append(limit)
    with sqlite_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def cat_keyword_stats(cid: str = None) -> dict:
    sql = ("SELECT kind, COUNT(*) rows, COUNT(DISTINCT keyword) words, "
           "COUNT(DISTINCT lcp_code) lcps, "
           "SUM(CASE WHEN banned IS NULL OR banned='' THEN 1 ELSE 0 END) ok, "
           "SUM(CASE WHEN views < 1000 AND views > 0 THEN 1 ELSE 0 END) low "
           "FROM cat_keyword")
    args = []
    if cid:
        sql += " WHERE cid = ?"
        args.append(str(cid))
    sql += " GROUP BY kind"
    with sqlite_conn() as conn:
        return {r["kind"]: dict(r) for r in conn.execute(sql, args).fetchall()}


def analyzed_lcp_set(folder_name: str = None) -> set:
    """상품분석이 끝난 LCP (lcode_attr 의 analysis_done 기준)."""
    sql = "SELECT DISTINCT lcp_code FROM lcode_attr WHERE analysis_done = 1"
    args = []
    if folder_name:
        sql += " AND folder_name = ?"
        args.append(folder_name)
    with sqlite_conn() as conn:
        return {r["lcp_code"] for r in conn.execute(sql, args).fetchall()}

def lcp_category_search(keyword: str = "", only_split: bool = False,
                        limit: int = 300) -> list:
    """
    카테고리 수정 화면용 검색. LCP 단위로 묶어 지금 저장된 카테고리를 보여준다.

    only_split 은 '한 LCP 안에서 L코드끼리 카테고리가 다른 것' 만 남긴다.
    자동 저장이 틀렸을 때 흔히 이 모습이 되므로 찾는 실마리가 된다.
    """
    kw = (keyword or "").strip()
    sql = ("SELECT a.lcp_code, a.l_code, a.etc_category, a.title_saved, "
           "       COALESCE(p.product_name, '') AS product_name "
           "FROM lcode_attr a "
           "LEFT JOIN lcp_product p ON p.lcp_code = a.lcp_code WHERE 1=1")
    args = []
    if kw:
        sql += (" AND (a.lcp_code LIKE ? OR a.l_code LIKE ? "
                "OR p.product_name LIKE ?)")
        args += [f"%{kw}%"] * 3
    sql += " ORDER BY a.lcp_code, a.l_code"

    groups = {}
    with sqlite_conn() as c:
        for r in c.execute(sql, args):
            g = groups.setdefault(r["lcp_code"], {
                "lcp_code": r["lcp_code"],
                "product_name": r["product_name"] or "",
                "n": 0, "done": 0, "cats_code": []})
            g["n"] += 1
            g["done"] += 1 if r["title_saved"] else 0
            code = str(r["etc_category"] or "")
            if code and code not in g["cats_code"]:
                g["cats_code"].append(code)

        # 코드 -> 이름. lcp_category 에 후보 이름이 이미 들어와 있다.
        names = {}
        for r in c.execute("SELECT DISTINCT code, name FROM lcp_category"):
            names[str(r["code"])] = r["name"]

    out = []
    for g in groups.values():
        if only_split and len(g["cats_code"]) < 2:
            continue
        g["cats"] = [names.get(x, x) for x in g["cats_code"]]
        g["cat_name"] = g["cats"][0] if len(g["cats"]) == 1 else ""
        out.append(g)
    out.sort(key=lambda x: (-len(x["cats_code"]), x["lcp_code"]))
    return out[:limit]


def lcode_rows_of(lcp_code: str, folder_name: str = None,
                  all_folders: bool = False) -> list:
    """
    한 LCP 의 L코드 목록.

    같은 LCP 가 여러 작업폴더에 걸쳐 있을 수 있다. 폴더를 안 거르면 지금
    작업폴더가 아닌 상품까지 손대게 되므로 **기본이 작업폴더 한정**이다.
    전부 보려면 all_folders=True 를 준다.
    """
    sql = ("SELECT l_code, product_no, etc_category, cat_saved, title_saved, "
           "       tag_count, title1, next_step, folder_name FROM lcode_attr "
           "WHERE lcp_code = ?")
    args = [lcp_code]
    if not all_folders:
        folder = folder_name or get_job_folder()
        if folder:
            sql += " AND folder_name = ?"
            args.append(folder)
    sql += " ORDER BY l_code"
    with sqlite_conn() as c:
        return [dict(r) for r in c.execute(sql, args)]

def tag_work_rows(folder_name: str = None, day: str = "",
                  limit: int = 2000) -> list:
    """
    자동으로 태그를 넣은 기록. 검수 화면이 이걸로 목록을 만든다.

    같은 L코드를 여러 번 손댔으면 마지막 것만 남긴다 — 사람이 보려는 건
    '지금 무엇이 들어가 있나' 이지 이력이 아니다.
    """
    sql = ("SELECT t.id, t.ts, t.lcp_code, t.l_code, t.product_no, t.picked, "
           "       t.picked_count, t.source, t.message, "
           "       COALESCE(p.product_name,'') product_name, "
           "       a.etc_category, a.title1, a.title_saved "
           "FROM task_log t "
           "LEFT JOIN lcp_product p ON p.lcp_code = t.lcp_code "
           "LEFT JOIN lcode_attr a ON a.product_no = t.product_no "
           "WHERE t.step = '태그'")
    args = []
    if folder_name:
        sql += " AND t.folder_name = ?"
        args.append(folder_name)
    if day:
        sql += " AND date(t.ts) = date(?)"
        args.append(day)
    sql += " ORDER BY t.id DESC"
    seen, out = set(), []
    with sqlite_conn() as c:
        for r in c.execute(sql, args):
            key = (r["lcp_code"], r["l_code"])
            if key in seen:
                continue
            seen.add(key)
            out.append(dict(r))
            if len(out) >= limit:
                break
    out.sort(key=lambda x: (x["lcp_code"], x["l_code"]))
    return out

def sync_info_status(folder_name: str, by_status: dict) -> dict:
    """
    `lcp_lcode.info_status` 를 사이트 현재값으로 맞춘다.

    상품명·태그를 저장하면 사이트가 상품정보를 '저장완료' 로 바꾼다
    (2026-09-05 실측 — 202건이 그렇게 넘어갔다). 그런데 우리 목록은 점검
    당시의 스냅샷이라, 동기화하지 않으면 이미 끝난 것이 계속 미작업으로
    보인다.

    by_status : {'저장완료': {L코드...}, '미작업': {...}, ...}
    반환      : {상태: 바뀐 행수}
    """
    ts = now_str()
    out = {}
    with sqlite_conn() as conn:
        for status, codes in (by_status or {}).items():
            codes = [c for c in codes if c]
            if not codes:
                continue
            n = 0
            for i in range(0, len(codes), 500):
                chunk = codes[i:i + 500]
                marks = ",".join("?" * len(chunk))
                cur = conn.execute(
                    f"UPDATE lcp_lcode SET info_status = ?, updated_at = ? "
                    f"WHERE folder_name = ? AND l_code IN ({marks}) "
                    f"AND info_status != ?",
                    [status, ts, folder_name, *chunk, status])
                n += cur.rowcount
            out[status] = n
    return out

def save_synonyms(pairs: list) -> dict:
    """
    동의어 대응을 쌓는다. pairs: [(surface, normal, example), ...]

    같은 대응을 여러 번 보면 `seen` 이 올라간다 — 자주 보이는 것일수록 믿을
    만하다. 한 번만 보인 것은 형태소 분석이 튄 것일 수 있다.
    """
    ts = now_str()
    n = 0
    with sqlite_conn() as c:
        for surface, normal, example in pairs:
            surface = (surface or "").strip()
            normal = (normal or "").strip()
            if not surface or not normal or surface == normal:
                continue
            c.execute(
                "INSERT INTO synonym (surface, normal, seen, example, "
                "                     first_seen, last_seen) "
                "VALUES (?,?,1,?,?,?) "
                "ON CONFLICT(surface, normal) DO UPDATE SET "
                "  seen = seen + 1, last_seen = excluded.last_seen",
                (surface, normal, example, ts, ts))
            n += 1

    mirror = ""
    conn2 = mysql_conn()
    if conn2 is not None:
        try:
            rows = [(a, b, c2, ts, ts) for a, b, c2 in pairs
                    if (a or "").strip() and (b or "").strip() and a != b]
            with conn2:
                mysql_prepare(conn2)
                with conn2.cursor() as cur:
                    for i in range(0, len(rows), 300):
                        cur.executemany(
                            "INSERT INTO LOHASAUTO_SYNONYM "
                            "(`surface`,`normal`,`seen`,`example`,"
                            " `first_seen`,`last_seen`) "
                            "VALUES (%s,%s,1,%s,%s,%s) "
                            "ON DUPLICATE KEY UPDATE "
                            "  `seen`=`seen`+1, `last_seen`=VALUES(`last_seen`)",
                            rows[i:i + 300])
            mirror = f"MySQL 미러 {len(rows):,}행"
        except Exception as e:
            mirror = f"MySQL 미러 실패: {e}"
    return {"rows": n, "mirror": mirror}


def synonym_map(min_seen: int = 1) -> dict:
    """{표기: 대표어}. 여러 대표어가 잡히면 가장 자주 본 것을 쓴다."""
    out = {}
    with sqlite_conn() as c:
        for r in c.execute(
                "SELECT surface, normal, seen FROM synonym WHERE seen >= ? "
                "ORDER BY seen DESC", (min_seen,)):
            out.setdefault(r["surface"], r["normal"])
    return out

_TITLE_BAN_SEED = [
    # 화장실·주방 설비 타사 브랜드. 후보 표에는 같은 카테고리의 남의 제품명이
    # 그대로 섞여 온다 - '이누스 미니세면대' 에 '대림세면대' 가 붙었다(2026-09-05).
    "대림", "이누스", "로얄토토", "아메리칸스탠다드", "계림", "아메리칸",
]


_TITLE_VOCAB = None


def title_word_vocab() -> set:
    """
    사람이 만든 상품명에 실제로 쓰인 낱말 전부(`title_pattern` 에서).

    여기 없는 말은 **상표일 가능성이 높다**. 4,723건을 학습한 사전이라
    평범한 말은 거의 다 들어 있다. 막지는 않고 뒤로 미는 용도다.
    """
    global _TITLE_VOCAB
    if _TITLE_VOCAB is None:
        try:
            with sqlite_conn() as c:
                _TITLE_VOCAB = {r["word"] for r in
                                c.execute("SELECT DISTINCT word FROM title_pattern")}
        except Exception:
            _TITLE_VOCAB = set()
    return _TITLE_VOCAB


_CAT_NAME = None


def category_name(code: str) -> str:
    """
    카테고리 코드의 이름. '... / 공기청정기필터' 처럼 상품 종류가 들어 있다.

    카테고리 이름에 있는 말은 그 상품에 **원래 있는 성질**이다. 공기청정기
    필터 상품의 상품명에서 '필터' 를 빼면 안 된다(2026-09-05).
    """
    global _CAT_NAME
    if _CAT_NAME is None:
        try:
            with sqlite_conn() as c:
                _CAT_NAME = {str(r["code"]): r["name"] or "" for r in
                             c.execute("SELECT DISTINCT code, name "
                                       "FROM lcp_category")}
        except Exception:
            _CAT_NAME = {}
    return _CAT_NAME.get(str(code or ""), "")


def save_lcode_image(l_code: str, product_no, d: dict) -> None:
    """
    대표이미지 URL 을 남긴다 (원본 / 현재(AI)).

    화면이 다시 열릴 때 빠르게 뜨라고 두는 것이고, **판단의 근거는 늘
    그때 받아온 값**이다 — AI 이미지는 나중에 생기기도 한다(2026-09-10).
    """
    with sqlite_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS lcode_image (
            l_code TEXT PRIMARY KEY, product_no TEXT, uid TEXT,
            ai INTEGER, org_main TEXT, edit_main TEXT,
            org_n INTEGER, edit_n INTEGER, updated_at TEXT)""")
        c.execute(
            "INSERT OR REPLACE INTO lcode_image (l_code, product_no, uid, ai,"
            " org_main, edit_main, org_n, edit_n, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (l_code, str(product_no), str(d.get("uid") or ""),
             1 if d.get("ai") else 0,
             (d.get("org") or {}).get("main1", ""),
             (d.get("edit") or {}).get("main1", ""),
             len((d.get("org") or {}).get("detail") or []),
             len((d.get("edit") or {}).get("detail") or []),
             now_str()))


def save_cat_rule(cid: str, scope: str, word: str, action: str,
                  note: str = "") -> None:
    """사용자 지적을 카테고리별로 남긴다. 다음에 같은 카테고리에서 쓴다."""
    with sqlite_conn() as c:
        c.execute("INSERT OR REPLACE INTO cat_rule "
                  "(cid, scope, word, action, note, created_at) "
                  "VALUES (?,?,?,?,?,?)",
                  (str(cid or ""), scope, word, action, note, now_str()))


def cat_rules(cid: str, scope: str = "title") -> dict:
    """
    그 카테고리에 쌓인 지침. 전체('') 규칙도 함께 준다.

    반환 {'ban': {...}, 'need_name': {...}, 'must': [...]}
    """
    out = {"ban": set(), "need_name": set(), "must": [], "loose": set()}
    try:
        with sqlite_conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS cat_rule ("
                      "cid TEXT NOT NULL, scope TEXT NOT NULL, "
                      "word TEXT NOT NULL, action TEXT NOT NULL, note TEXT, "
                      "created_at TEXT, PRIMARY KEY (cid, scope, word, action))")
            for r in c.execute(
                    "SELECT word, action FROM cat_rule "
                    "WHERE cid IN ('', ?) AND scope IN ('both', ?)",
                    (str(cid or ""), scope)):
                if r["action"] == "must":
                    out["must"].append(r["word"])
                elif r["action"] == "loose":
                    out["loose"].add(r["word"])
                elif r["action"] in out:
                    out[r["action"]].add(r["word"])
    except Exception:
        pass
    return out


def cat_rule_list(cid: str = "") -> list:
    """화면·보고용 목록."""
    sql = ("SELECT cid, scope, word, action, note, created_at FROM cat_rule")
    a = []
    if cid:
        sql += " WHERE cid IN ('', ?)"
        a.append(str(cid))
    sql += " ORDER BY created_at DESC"
    with sqlite_conn() as c:
        return [dict(r) for r in c.execute(sql, a)]


def title_bans() -> set:
    """상품명에 넣지 않을 말(타사 브랜드 등). 원상품명에 있으면 예외로 허용한다."""
    out = set(_TITLE_BAN_SEED)
    try:
        with sqlite_conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS title_ban ("
                      "word TEXT PRIMARY KEY, memo TEXT, updated_at TEXT)")
            out |= {r["word"] for r in c.execute("SELECT word FROM title_ban")}
    except Exception:
        pass
    return {w for w in out if w}


def add_title_ban(words, memo: str = "") -> int:
    """타사 브랜드를 사전에 추가한다(사용자가 잡아준 것을 쌓는 자리)."""
    ts = now_str()
    rows = [(w.strip(), memo, ts) for w in (words or []) if w and w.strip()]
    if not rows:
        return 0
    with sqlite_conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS title_ban ("
                  "word TEXT PRIMARY KEY, memo TEXT, updated_at TEXT)")
        c.executemany("INSERT OR REPLACE INTO title_ban (word, memo, updated_at)"
                      " VALUES (?,?,?)", rows)
    return len(rows)


def save_fix10_log(run_id: str, folder: str, row: dict, direction: str,
                   res: dict, seconds: float = 0.0) -> None:
    """수정사항 1.0 처리 한 건을 남긴다. 실패해도 작업은 멈추지 않는다."""
    try:
        with sqlite_conn() as c:
            c.execute(
                "INSERT INTO fix10_log (run_id, folder_name, product_code, "
                "product_id, direction, result, message, seconds, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, folder, row.get("product_code", ""),
                 str(row.get("product_id", "")), direction,
                 res.get("result", ""), (res.get("message") or "")[:200],
                 round(float(seconds or 0), 2), now_str()))
    except Exception:
        pass


def fix10_runs(limit: int = 20) -> list:
    """최근 실행 요약. 화면에 '언제 몇 건 돌렸나' 를 보여주는 값이다."""
    with sqlite_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT run_id, folder_name, MIN(created_at) started, "
            "       MAX(created_at) ended, COUNT(*) n, "
            "       SUM(result='ok') ok, SUM(result='soldout') soldout, "
            "       SUM(result='fail') fail "
            "FROM fix10_log GROUP BY run_id "
            "ORDER BY started DESC LIMIT ?", (limit,))]


def fix10_last_run(folder: str = "") -> dict:
    """그 폴더를 마지막으로 돌린 시각. 주 1회 자동 실행 판단에 쓴다."""
    sql = "SELECT MAX(created_at) t FROM fix10_log"
    args = []
    if folder:
        sql += " WHERE folder_name = ?"
        args.append(folder)
    with sqlite_conn() as c:
        r = c.execute(sql, args).fetchone()
    return {"at": (r["t"] if r else "") or ""}


def save_title_backup(run_id: str, rows: list) -> int:
    """상품명을 바꾸기 전에 지금 값을 남긴다. 되돌릴 근거다."""
    ts = now_str()
    data = [(run_id, r.get("folder_name", ""), r.get("lcp_code", ""),
             r.get("l_code", ""), str(r.get("product_no", "")),
             str(r.get("etc_category", "")), r.get("title1", ""),
             r.get("product_name", ""), r.get("reason", ""), ts)
            for r in (rows or []) if r.get("l_code")]
    if not data:
        return 0
    with sqlite_conn() as c:
        c.executemany(
            "INSERT INTO title_backup (run_id, folder_name, lcp_code, l_code,"
            " product_no, etc_category, title1, product_name, reason,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?)", data)
    return len(data)


def title_backups(run_id: str = "", l_code: str = "") -> list:
    sql = "SELECT * FROM title_backup WHERE 1=1"
    a = []
    if run_id:
        sql += " AND run_id = ?"
        a.append(run_id)
    if l_code:
        sql += " AND l_code = ?"
        a.append(l_code)
    sql += " ORDER BY id"
    try:
        with sqlite_conn() as c:
            return [dict(r) for r in c.execute(sql, a)]
    except Exception:
        return []


def title_backup_runs(limit: int = 20) -> list:
    """언제 무엇을 몇 건 백업했나."""
    try:
        with sqlite_conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT run_id, MIN(created_at) at, COUNT(*) n "
                "FROM title_backup GROUP BY run_id "
                "ORDER BY at DESC LIMIT ?", (limit,))]
    except Exception:
        return []


def save_ai_job(rec: dict) -> int:
    """
    AI 이미지 일괄수정 **한 페이지** 결과를 남긴다. 로컬 + 서버 양쪽에.

    이 기록이 있어야 "엑사는 1·2페이지 했다" 를 프로그램이 안다 —
    다음에 열 때 시작 페이지를 자동으로 3으로 잡는다(2026-09-07 사용자).
    """
    ts = now_str()
    row = (rec.get("folder") or rec.get("folder_name") or "",
           int(rec.get("page") or 0), str(rec.get("no") or ""),
           rec.get("title") or "", rec.get("query") or "",
           int(rec.get("count") or 0), str(rec.get("first") or ""),
           str(rec.get("last") or ""), rec.get("state") or "",
           rec.get("started") or "", rec.get("ended") or "", ts)
    local_id = 0
    try:
        with sqlite_conn() as c:
            cur = c.execute(
                "INSERT INTO ai_image_job (folder_name, page, job_no, title, "
                "query, row_count, first_no, last_no, state, started_at, "
                "ended_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(folder_name, page, title) DO UPDATE SET "
                "job_no=excluded.job_no, state=excluded.state, "
                "row_count=excluded.row_count, "
                "started_at=excluded.started_at, ended_at=excluded.ended_at",
                row)
            local_id = cur.lastrowid or 0
            if not local_id:
                r = c.execute(
                    "SELECT id FROM ai_image_job WHERE folder_name=? AND "
                    "page=? AND title=?", (row[0], row[1], row[3])).fetchone()
                local_id = r["id"] if r else 0
    except Exception:
        return 0
    _mirror_ai_job(local_id, row)
    return local_id


def _mirror_ai_job(local_id: int, row) -> None:
    conn = mysql_conn()
    if conn is None or not local_id:
        return
    try:
        with conn:
            with conn.cursor() as cur:
                for ddl in MYSQL_DDL:
                    cur.execute(ddl)
                cur.execute(
                    "INSERT INTO LOHASAUTO_AI_IMAGE_JOB (local_id, "
                    "folder_name, page, job_no, title, query, row_count, "
                    "first_no, last_no, state, started_at, ended_at, "
                    "created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                    "%s,%s) ON DUPLICATE KEY UPDATE job_no=VALUES(job_no), "
                    "state=VALUES(state), row_count=VALUES(row_count), "
                    "started_at=VALUES(started_at), ended_at=VALUES(ended_at)",
                    (local_id,) + tuple(row))
    except Exception:
        pass


def ai_jobs(folder: str = "", limit: int = 200) -> list:
    """AI 이미지 일괄수정 이력 (최근 순)."""
    sql = "SELECT * FROM ai_image_job"
    a = []
    if folder:
        sql += " WHERE folder_name = ?"
        a.append(folder)
    sql += " ORDER BY id DESC LIMIT ?"
    a.append(limit)
    try:
        with sqlite_conn() as c:
            return [dict(r) for r in c.execute(sql, a)]
    except Exception:
        return []


def ai_done_pages(folder: str) -> set:
    """그 폴더에서 **이미 완료한** 페이지 번호. 중복 작업을 막는 근거다."""
    try:
        with sqlite_conn() as c:
            return {r["page"] for r in c.execute(
                "SELECT DISTINCT page FROM ai_image_job "
                "WHERE folder_name = ? AND state = '완료'", (folder,))}
    except Exception:
        return set()


def save_title_patterns(cid: str, counts: dict, n_titles: int) -> int:
    """카테고리별 '사람이 더 넣은 말' 을 저장한다."""
    ts = now_str()
    rows = [(str(cid), w, n, n_titles, ts) for w, n in (counts or {}).items()
            if w and n > 0]
    if not rows:
        return 0
    with sqlite_conn() as c:
        c.execute("DELETE FROM title_pattern WHERE cid = ?", (str(cid),))
        c.executemany(
            "INSERT INTO title_pattern (cid, word, n, titles, updated_at) "
            "VALUES (?,?,?,?,?)", rows)
    return len(rows)


def title_patterns(cid: str, min_n: int = 2) -> dict:
    """{말: 횟수}. 그 카테고리 상품명에 자주 들어간 말."""
    with sqlite_conn() as c:
        return {r["word"]: r["n"] for r in c.execute(
            "SELECT word, n FROM title_pattern WHERE cid = ? AND n >= ? "
            "ORDER BY n DESC", (str(cid), min_n))}
