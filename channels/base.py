"""
ChannelAdapter 인터페이스 (Phase 0 stub).

모든 판매 채널은 이 인터페이스를 구현해 입력 어댑터/포스트백 어댑터 역할을 한다.
실제 메서드 시그니처는 Phase 1(Qoo10 일본 이전) 시점에 첫 구체 구현을 보고 확정한다.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import datetime


@dataclass
class Order:
    """채널 무관 통합 주문 모델 (Phase 0 초안 — Phase 1에서 확장 예정)."""
    channel: str                      # 채널 식별자 (예: 'qoo10_japan', 'cachers_domestic')
    brand: str                        # 화주 ('캐처스' or '네뉴')
    order_no: str                     # 채널 주문번호
    cart_no: str                      # 합포장 단위 (없으면 order_no)
    sku_code: str                     # 우리 SKU
    qty: int
    recipient_name: str = ''
    recipient_phone: str = ''
    postal_code: str = ''
    address: str = ''
    order_date: Optional[datetime.date] = None
    raw: Dict = field(default_factory=dict)  # 원본 채널 데이터


class ChannelAdapter(ABC):
    """채널별 입력/출력 어댑터 인터페이스."""

    channel_id: str = ''           # 'qoo10_japan' 등 식별자
    channel_name: str = ''         # 표시명
    brand: str = ''                # '캐처스' or '네뉴'
    output_id: str = ''            # 'daone' / 'kse_japan' / 'eza' / ...

    @abstractmethod
    def fetch_orders(self, **kwargs) -> List[Order]:
        """주문 가져오기 — API 호출 또는 파일 파싱."""
        raise NotImplementedError

    def post_waybill(self, mappings: Dict[str, str]) -> Dict:
        """송장 회신 — 채널이 받지 않거나 EZA가 자동 처리하면 빈 구현 OK.

        Args:
            mappings: {order_no: tracking_no, ...}
        Returns:
            {'ok': bool, 'results': [...], 'errors': [...]}
        """
        return {'ok': True, 'results': [], 'errors': []}
