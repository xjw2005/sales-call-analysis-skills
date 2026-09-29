"""visit_pipeline

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-30 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('visit_pipeline',
    sa.Column('visit_id', sa.Integer(), nullable=False),
    sa.Column('stage', sa.String(length=16), nullable=False),
    sa.Column('las_tasks', sa.JSON(), nullable=True),
    sa.Column('error_stage', sa.String(length=16), nullable=False),
    sa.Column('error', sa.Text(), nullable=False),
    sa.Column('uncertain', sa.Boolean(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('usage', sa.JSON(), nullable=True),
    sa.Column('validity', sa.JSON(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['visit_id'], ['visits.id'], ),
    sa.PrimaryKeyConstraint('visit_id')
    )
    with op.batch_alter_table('visit_pipeline', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_visit_pipeline_stage'), ['stage'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('visit_pipeline', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_visit_pipeline_stage'))
    op.drop_table('visit_pipeline')
