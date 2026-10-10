"""practice_sessions

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-13 12:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('practice_sessions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('scenario_id', sa.String(length=40), nullable=False),
    sa.Column('total_rounds', sa.Integer(), nullable=False),
    sa.Column('turns', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('tokens', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('practice_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_practice_sessions_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_practice_sessions_created_at'), ['created_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('practice_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_practice_sessions_created_at'))
        batch_op.drop_index(batch_op.f('ix_practice_sessions_user_id'))
    op.drop_table('practice_sessions')
