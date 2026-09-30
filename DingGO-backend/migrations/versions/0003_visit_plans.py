"""visit_plans

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-30 12:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('visit_plans',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('plan_date', sa.Date(), nullable=False),
    sa.Column('store_id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('reason', sa.String(length=255), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('visit_id', sa.Integer(), nullable=True),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['visit_id'], ['visits.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'plan_date', 'store_id')
    )
    with op.batch_alter_table('visit_plans', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_visit_plans_plan_date'), ['plan_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_visit_plans_store_id'), ['store_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_visit_plans_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('visit_plans', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_visit_plans_user_id'))
        batch_op.drop_index(batch_op.f('ix_visit_plans_store_id'))
        batch_op.drop_index(batch_op.f('ix_visit_plans_plan_date'))
    op.drop_table('visit_plans')
