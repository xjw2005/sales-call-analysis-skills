"""门店、拜访的删除改为隐藏（deleted_at / deleted_by），管理员可恢复

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-12 12:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ('stores', 'visits'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('deleted_at', sa.DateTime(), nullable=True))
            batch_op.add_column(sa.Column('deleted_by', sa.Integer(), nullable=True))
            batch_op.create_index(batch_op.f(f'ix_{table}_deleted_at'), ['deleted_at'], unique=False)
            batch_op.create_foreign_key(f'fk_{table}_deleted_by_users', 'users', ['deleted_by'], ['id'])


def downgrade() -> None:
    for table in ('stores', 'visits'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(f'fk_{table}_deleted_by_users', type_='foreignkey')
            batch_op.drop_index(batch_op.f(f'ix_{table}_deleted_at'))
            batch_op.drop_column('deleted_by')
            batch_op.drop_column('deleted_at')
