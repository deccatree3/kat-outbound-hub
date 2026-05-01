"""
OutputBuilder 인터페이스 (Phase 0 stub).

출력물 종류: 다원 발주서 / EZA 발주서 / KSE Outbound / 번들작업파일 / 부착문서 / 바코드라벨 / CI·PL 등
구체 시그니처는 Phase 1, 2에서 첫 구현을 보고 확정.
"""
from abc import ABC, abstractmethod
from typing import List

from channels.base import Order


class OutputBuilder(ABC):
    """출고요청서/발주서/부속문서 생성 인터페이스."""

    output_id: str = ''       # 'daone_korea' / 'kse_japan' / 'eza_korea' / 'bundle' 등
    output_name: str = ''     # 표시명
    file_extension: str = 'xlsx'

    @abstractmethod
    def build(self, orders: List[Order], **kwargs) -> bytes:
        """orders → 출력물 bytes."""
        raise NotImplementedError

    def filename(self, **kwargs) -> str:
        """다운로드 파일명. 기본 구현은 output_id + 오늘 날짜."""
        import datetime
        today = datetime.date.today().strftime('%Y%m%d')
        return f"{self.output_id}_{today}.{self.file_extension}"
