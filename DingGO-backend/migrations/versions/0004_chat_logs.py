"""chat_logs

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-10 12:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('chat_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('question', sa.String(length=500), nullable=False),
    sa.Column('reply', sa.Text(), nullable=False),
    sa.Column('store_id', sa.Integer(), nullable=True),
    sa.Column('tools', sa.JSON(), nullable=True),
    sa.Column('rounds', sa.Integer(), nullable=False),
    sa.Column('tokens', sa.Integer(), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.Column('first_token_ms', sa.Integer(), nullable=True),
    sa.Column('error', sa.Text(), nullable=False),
    sa.Column('rating', sa.Integer(), nullable=True),
    sa.Column('feedback_note', sa.String(length=500), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('chat_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chat_logs_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_logs_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('chat_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_chat_logs_user_id'))
        batch_op.drop_index(batch_op.f('ix_chat_logs_created_at'))
    op.drop_table('chat_logs')
