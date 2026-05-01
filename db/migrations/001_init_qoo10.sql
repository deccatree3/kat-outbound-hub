-- 001_init_qoo10.sql
-- Qoo10 일본 출고 모듈 4 테이블 초기 스키마.
-- 자매 프로젝트(kat-kse-3pl-japan)의 운영 스키마와 1:1 호환.
-- 새 Supabase 프로젝트의 SQL Editor에서 한 번 실행.

-- 1. 자격증명 (단일 행, id=1 고정 upsert)
CREATE TABLE IF NOT EXISTS qoo10_credentials (
    id INTEGER PRIMARY KEY,
    api_key TEXT,
    user_id TEXT,
    password TEXT,
    expires_at DATE,
    updated_at TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')
);

-- 2. Qoo10 상품/옵션 → SKU 매핑 (PK=상품명+옵션)
CREATE TABLE IF NOT EXISTS qoo10_product_mapping (
    qoo10_name   TEXT NOT NULL,
    qoo10_option TEXT NOT NULL DEFAULT '',
    item_codes   TEXT,         -- "상품명1,상품명2" (표시용)
    sku_codes    TEXT,         -- "SKU1,SKU2"
    quantities   TEXT,         -- "1,2" (SKU별 수량)
    enabled      BOOLEAN DEFAULT TRUE,
    updated_at   TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul'),
    PRIMARY KEY (qoo10_name, qoo10_option)
);

-- 3. brief.csv 임시 저장소 (Step 4~5 사이의 in-flight 데이터)
CREATE TABLE IF NOT EXISTS qoo10_pending_brief (
    id              BIGSERIAL PRIMARY KEY,
    file_name       TEXT,
    content         BYTEA,
    cart_count      INTEGER,
    disabled_count  INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul'),
    consumed_at     TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_qoo10_pending_brief_active
    ON qoo10_pending_brief (file_name) WHERE consumed_at IS NULL;

-- 4. 출고 이력 + 송장
CREATE TABLE IF NOT EXISTS qoo10_outbound (
    qoo10_cart_no       TEXT NOT NULL,
    qoo10_order_no      TEXT NOT NULL DEFAULT '',
    sku_code            TEXT NOT NULL,
    sku_name            TEXT,
    planned_qty         INTEGER,
    recipient           TEXT,
    recipient_phone     TEXT,
    postal_code         TEXT,
    address             TEXT,
    qoo10_product_name  TEXT,
    qoo10_option        TEXT,
    qoo10_qty           INTEGER,
    source_file         TEXT,
    waybill             TEXT,
    waybill_updated_at  TIMESTAMP,
    generated_at        TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul'),
    PRIMARY KEY (qoo10_cart_no, qoo10_order_no, sku_code)
);
CREATE INDEX IF NOT EXISTS idx_qoo10_outbound_generated_at
    ON qoo10_outbound (generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_qoo10_outbound_waybill
    ON qoo10_outbound (waybill) WHERE waybill IS NOT NULL;
