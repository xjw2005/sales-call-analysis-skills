"""user_memories

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-11 12:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('chat_sessions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('summary', sa.Text(), nullable=True))  # MySQL 的 TEXT 列不能有默认值，所以允许为空
        batch_op.add_column(sa.Column('summary_upto', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('state', sa.JSON(), nullable=True))
    op.create_table('user_memories',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value', sa.JSON(), nullable=True),
    sa.Column('scope', sa.String(length=8), nullable=False),
    sa.Column('trust', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('reason', sa.String(length=255), nullable=False),
    sa.Column('session_id', sa.Integer(), nullable=True),
    sa.Column('hits', sa.Integer(), nullable=False),
    sa.Column('last_used_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'kind', 'key', 'scope')
    )
    with op.batch_alter_table('user_memories', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_memories_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_memories_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('user_memories', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_memories_user_id'))
        batch_op.drop_index(batch_op.f('ix_user_memories_status'))
    op.drop_table('user_memories')
    with op.batch_alter_table('chat_sessions', schema=None) as batch_op:
        batch_op.drop_column('state')
        batch_op.drop_column('summary_upto')
        batch_op.drop_column('summary')
