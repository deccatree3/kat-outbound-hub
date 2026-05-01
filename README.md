# KAT Outbound Hub

캐처스/네뉴 출고 통합 자동화 시스템.

## 실행

```powershell
cd C:\claude\kat-outbound-hub
python -m streamlit run dashboard.py
```

## 설정

1. `config.json.example` → `config.json` 복사 후 값 채우기 (gitignored)
2. `database_url`, `qoo10_*` 등 자격증명 입력

## 구조

```
kat-outbound-hub/
├── dashboard.py              # Streamlit 메인 진입점
├── channels/                 # 채널별 입력 어댑터 (Qoo10/캐처스국내몰/...)
│   ├── base.py
│   └── {channel}/...
├── outputs/                  # 출력 빌더 (다원 발주서/KSE Outbound/EZA 발주서/...)
│   ├── base.py
│   └── {output}/...
├── db/                       # DB 헬퍼 (Phase 1+에서 추가)
└── CLAUDE.md                 # 프로젝트 컨텍스트 + 결정 히스토리
```

## 단계 로드맵

자세한 내용은 `CLAUDE.md` 참고.

- Phase 0: 골격 (현재)
- Phase 1: Qoo10 일본 이전 (kat-kse-3pl-japan에서)
- Phase 2: MVP — 캐처스 국내몰 다원 발주서
- Phase 3+: 나머지 채널/출력물

## 자매 프로젝트

- `C:\claude\kat-kse-3pl-japan` — 일본 KSE 물류비 검토 + 재고 소진 예측 + Qoo10 일본 출고 (운영 중)
