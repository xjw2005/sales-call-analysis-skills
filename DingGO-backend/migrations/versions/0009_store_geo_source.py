"""stores.geo_source：门店坐标是怎么来的（manual 人工设置 / geocode 地址解析），人工设置的不会被批量解析覆盖

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-14 12:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('stores', schema=None) as batch_op:
        batch_op.add_column(sa.Column('geo_source', sa.String(length=16), nullable=False, server_default=''))


def downgrade() -> None:
    with op.batch_alter_table('stores', schema=None) as batch_op:
        batch_op.drop_column('geo_source')
