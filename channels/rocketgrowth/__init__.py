"""로켓그로스 채널 공통 모듈.

캐처스(`cachers_rocketgrowth`) 와 네뉴(`nenu_rocketgrowth`) 가 같은 워크플로우 사용 —
화주(`brand`) 인자 1개로 결과물 분기.

3 탭 구조:
  탭 1: 발주 계획 (공통)
  탭 2: 결과물 패키지 (운송방식 + 화주 분기)
  탭 3: 송장 후처리 (화주 분기 — 네뉴는 이지어드민 송장 양식 생성)

자매 프로젝트(nn-rocketgrowth_inventory) 의 입고_발주_관리.py(2159줄) 이전 — Phase C~E 진행.
"""
