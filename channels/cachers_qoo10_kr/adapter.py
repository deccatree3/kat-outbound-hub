"""
캐처스 큐텐 국내출고 ChannelAdapter — KSE OMS 주문내역.xlsx → 다원 19컬럼 발주서.

흐름: Qoo10 일본 주문 중 한국 다원 → KSE 한국 물류센터 → 일본 배송 흐름.
입력은 KSE OMS에서 다운받은 주문내역(.xlsx).
"""
from typing import Dict, List

from channels.base import ChannelAdapter, Order
from outputs.daone.builder import parse_kse_oms_xlsx


class CachersQoo10KrAdapter(ChannelAdapter):
    channel_id = 'cachers_qoo10_kr'
    channel_name = '캐처스 큐텐 국내'
    brand = '캐처스'
    output_id = 'daone'

    def fetch_orders(self, kse_xlsx_bytes: bytes = None, **kwargs) -> List[Order]:
        if not kse_xlsx_bytes:
            return []
        rows = parse_kse_oms_xlsx(kse_xlsx_bytes)
        out: List[Order] = []
        for d in rows:
            try:
                qty = int(float(d.get('수량', 0))) if d.get('수량') else 0
            except (ValueError, TypeError):
                qty = 0
            out.append(Order(
                channel=self.channel_id,
                brand=self.brand,
                order_no=str(d.get('주문번호', '')),
                cart_no=str(d.get('접수번호') or d.get('주문번호', '')),
                sku_code='',
                qty=qty,
                recipient_name=d.get('받는사람', ''),
                recipient_phone=d.get('받는사람전화', ''),
                postal_code=d.get('우편번호', ''),
                address=d.get('주소', ''),
                raw=d,
            ))
        return out

    def post_waybill(self, mappings: Dict[str, str]) -> Dict:
        """송장은 이미 KSE가 발급(도착지송장번호) — 별도 회신 없음."""
        return {'ok': True, 'results': [], 'errors': []}
