"""inbound_plan_pallet_entry 테이블 추가 — 팔레트 적재 단위 저장.

같은 SKU 가 여러 팔레트로 분할되는 케이스(박스수 ≥ pallet_size)를 정확히 보존하기 위해,
'팔레트의 한 적재 행 = 한 row' 단위로 저장한다. InboundPlanItem.pallet_no 는 deprecated.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inbound_plan_pallet_entry",
        sa.Column(
            "plan_id",
            sa.BigInteger,
            sa.ForeignKey("inbound_plan.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pallet_no", sa.Integer, nullable=False),
        sa.Column("coupang_option_id", sa.BigInteger, nullable=False),
        sa.Column("boxes", sa.Integer, nullable=False),
        sa.Column("qty", sa.Integer, nullable=False),
        sa.Column("seq", sa.Integer, nullable=True),
        sa.PrimaryKeyConstraint(
            "plan_id", "pallet_no", "coupang_option_id",
            name="pk_inbound_plan_pallet_entry",
        ),
        sa.Index(
            "ix_inbound_plan_pallet_entry_plan_pallet",
            "plan_id", "pallet_no",
        ),
    )


def downgrade():
    op.drop_table("inbound_plan_pallet_entry")
