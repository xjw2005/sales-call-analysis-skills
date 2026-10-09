"""bind_codes

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-12 12:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('bind_codes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('used_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code_hash')
    )
    with op.batch_alter_table('bind_codes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_bind_codes_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('bind_codes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_bind_codes_user_id'))
    op.drop_table('bind_codes')
