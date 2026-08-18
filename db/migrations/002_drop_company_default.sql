-- 002_drop_company_default.sql
-- company_name 기본값 '서현' 제거.
--   배경: 다운로드 양식에 업체 컬럼이 없어 "다운→수정→재업로드" 시 파서가 업체를
--   못 읽고, 모델/서버 기본값 '서현'이 박혀 캐처스 상품이 서현으로 오분류되던 사고.
--   (CICARED-CC 번들 재고/소비기한 미인식) 다운로드 업체 컬럼 추가(c7db035)와 함께
--   기본값 자체를 제거해 재발을 원천 차단한다.
--
--   마스터 2곳(wms_product/coupang_product): DROP DEFAULT + DROP NOT NULL.
--     → 업로드가 업체를 빠뜨리면 조용히 서현이 아니라 NULL 로 남아 어느 브랜드 탭에도
--       안 뜨므로 정정 필요가 드러난다.
--   계획/로그 2곳(inbound_plan/coupang_result_log): DROP DEFAULT 만 (NOT NULL 유지).
--     → 앱이 저장 시 항상 company_name 을 명시 전달하므로 NULL 이 될 일이 없다.
--
--   적용: 2026-08-18 라이브 DB 직접 적용됨.
--   주의: 라이브 앱의 읽기 락과 데드락이 나므로 각 문장을 개별 트랜잭션 +
--         lock_timeout + 재시도로 실행할 것(한 트랜잭션에 여러 테이블 락 금지).

ALTER TABLE wms_product         ALTER COLUMN company_name DROP DEFAULT;
ALTER TABLE wms_product         ALTER COLUMN company_name DROP NOT NULL;
ALTER TABLE coupang_product     ALTER COLUMN company_name DROP DEFAULT;
ALTER TABLE coupang_product     ALTER COLUMN company_name DROP NOT NULL;
ALTER TABLE inbound_plan        ALTER COLUMN company_name DROP DEFAULT;
ALTER TABLE coupang_result_log  ALTER COLUMN company_name DROP DEFAULT;
