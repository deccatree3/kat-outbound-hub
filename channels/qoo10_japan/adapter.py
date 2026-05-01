"""
Qoo10 일본 ChannelAdapter — `qoo10/api_client`/`qoo10/generator`에 위임.

이 어댑터는 Phase 1 호환 계층. 다른 채널과의 균형을 위해 ChannelAdapter
인터페이스(channels/base.py)를 만족시킨다. 실제 Streamlit 페이지(page.py)는
qgen/qapi 모듈을 직접 호출하므로 이 어댑터는 자동화 시나리오(향후 cron/webhook)
와 다른 채널의 reference로 쓰임.
"""
from typing import Dict, List

from channels.base import ChannelAdapter, Order
from qoo10 import api_client as qapi
from qoo10 import generator as qgen


class Qoo10JapanAdapter(ChannelAdapter):
    channel_id = 'qoo10_japan'
    channel_name = 'Qoo10 일본'
    brand = '캐처스'
    output_id = 'kse_japan'

    def fetch_orders(self, days: int = 30, **kwargs) -> List[Order]:
        """QSM API로 신규주문(ShippingStat=2) 조회 → Order 리스트로 변환."""
        qsm_dicts, raw = qapi.fetch_orders_as_qsm_dicts(days=days)
        out: List[Order] = []
        for q in qsm_dicts:
            out.append(Order(
                channel=self.channel_id,
                brand=self.brand,
                order_no=str(q.get('주문번호', '')),
                cart_no=str(q.get('장바구니번호', '') or q.get('주문번호', '')),
                sku_code='',
                qty=int(q.get('수량', 1) or 1),
                recipient_name=q.get('수취인명', ''),
                recipient_phone=q.get('수취인핸드폰번호') or q.get('수취인전화번호') or '',
                postal_code=q.get('우편번호', ''),
                address=q.get('주소', ''),
                raw=q,
            ))
        return out

    def post_waybill(self, mappings: Dict[str, str]) -> Dict:
        """{order_no: tracking_no} → QSM API 일괄 등록.
        주의: 이 어댑터에서는 QSM 주문번호(orderNo) 단위로 처리.
        UI에서는 cart_no 단위로 송장 매칭 후 order_no로 펼쳐서 호출함.
        """
        if not mappings:
            return {'ok': True, 'results': [], 'errors': []}
        try:
            sak = qapi.get_sak()
        except Exception as ex:
            return {'ok': False, 'results': [], 'errors': [str(ex)]}
        pairs = [(o, w) for o, w in mappings.items() if o and w]
        results = qapi.register_waybills_batch(sak, pairs)
        errors = [r for r in results if not r['ok']]
        return {'ok': len(errors) == 0, 'results': results, 'errors': errors}
