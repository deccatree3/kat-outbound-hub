# KAT/NN Outbound Hub

캐처스/네뉴 출고 통합 자동화 시스템.

## 실행

```bash
python -m streamlit run dashboard.py
```

## 초기 설정

현재 DB 정책: **자매 프로젝트(`kat-kse-3pl-japan`) Supabase 공유** (Free 한도 제약).

1. `config.json.example` → `config.json` 복사
2. `database_url` 에 **자매 프로젝트의 DSN을 그대로 입력** (`kat-kse-3pl-japan/config.json` 의 `database_url` 값 복사)
3. Streamlit Cloud 배포 시: `.streamlit/secrets.toml` 에 같은 값 등록 (`.streamlit/secrets.toml.example` 참고)

> `db/migrations/001_init_qoo10.sql` 과 `scripts/migrate_qoo10_seed.py` 는 향후 DB 분리 시점에 쓰기 위한 자료. 현재 단계에서는 실행할 필요 없음.

## 구조

```
kat-outbound-hub/
├── dashboard.py                 # Streamlit 메인 진입점, 채널 디스패치
├── channels/                    # 채널별 입력 어댑터 + 페이지
│   ├── base.py                  # ChannelAdapter 인터페이스 + Order 모델
│   └── qoo10_japan/             # Phase 1: Qoo10 일본 (운영)
│       ├── adapter.py           # ChannelAdapter 구현
│       └── page.py              # 6단계 stepper UI
├── qoo10/                       # Qoo10 QAPI 클라이언트 + KSE Outbound 빌더
│   ├── api_client.py
│   ├── generator.py
│   └── templates/outbound_template.xlsx
├── outputs/                     # 출력 빌더 (Phase 2+에서 추가)
├── db/
│   ├── pg.py                    # Postgres 연결 헬퍼
│   └── migrations/              # SQL 마이그레이션
├── scripts/
│   └── migrate_qoo10_seed.py    # 자매 프로젝트 → 신규 DB 시드 이전
└── CLAUDE.md                    # 프로젝트 컨텍스트 + 결정 히스토리
```

## 단계 로드맵

자세한 내용은 `CLAUDE.md` 참고.

| Phase | 내용 | 상태 |
|:-:|------|:-:|
| 0 | 프로젝트 골격 | ✅ |
| 1 | Qoo10 일본 이전 | ✅ |
| 2 | MVP — 캐처스 국내몰 다원 발주서 | TODO |
| 3 | 캐처스 케이스 #4 채널들 | TODO |
| 4 | 네뉴 EZA 발주서 빌더 | TODO |
| 5+ | 번들/부착문서/CI·PL/바코드 | TODO |

## 자매 프로젝트

- `C:\claude\kat-kse-3pl-japan` — 일본 KSE 물류비 검토 + 재고 소진 예측 (운영 중).
  Qoo10 일본 출고 메뉴는 이 프로젝트(`kat-outbound-hub`)가 자리 잡으면 제거 예정.
