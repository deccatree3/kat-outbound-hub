"""
국내몰 ChannelAdapter — 캐처스/네뉴 통합 (이지오토 Y 흐름 공용).

EZA 확장주문검색.xls 를 받아 화주별 출력으로 분기.
캐처스: 다원 발주서.xlsx (수기 등록)
네뉴: 다원 발주서.xlsx (현재는 캐처스와 동일 양식. 향후 번들파일 등 분기 가능)

송장 회신은 EZA가 처리하므로 post_waybill은 no-op.
"""
from typing import Dict, List

from channels.base import ChannelAdapter, Order
from outputs.daone.builder import parse_eza_xls


class DomesticAdapter(ChannelAdapter):
    channel_id = 'domestic'
    channel_name = '국내몰 (캐처스/네뉴)'
    brand = '캐처스+네뉴'   # 화주는 페이지에서 선택
    output_id = 'daone'

    def __init__(self, brand: str = '캐처스'):
        """brand: '캐처스' 또는 '네뉴' (페이지에서 선택된 값을 주입)"""
        self.brand = brand

    def fetch_orders(self, eza_bytes: bytes = None, **kwargs) -> List[Order]:
        """EZA xls bytes(신양식) → Order 리스트."""
        if not eza_bytes:
            return []
        rows = parse_eza_xls(eza_bytes)
        out: List[Order] = []
        for d in rows:
            qty = d.get('상품수량') or 1
            try:
                qty = int(float(qty))
            except (ValueError, TypeError):
                qty = 1
            out.append(Order(
                channel=self.channel_id,
                brand=self.brand,
                order_no=str(d.get('주문번호', '')),
                cart_no=str(d.get('출하의뢰번호') or d.get('주문번호', '')),
                sku_code=str(d.get('제품코드') or d.get('상품메모') or d.get('바코드') or ''),
                qty=qty,
                recipient_name=d.get('수취인명', ''),
                recipient_phone=d.get('수취인연락처2') or d.get('수취인연락처1') or '',
                postal_code=d.get('수취인우편번호', ''),
                address=d.get('수취인주소1', ''),
                raw=d,
            ))
        return out

    def post_waybill(self, mappings: Dict[str, str]) -> Dict:
        return {'ok': True, 'results': [], 'errors': []}
