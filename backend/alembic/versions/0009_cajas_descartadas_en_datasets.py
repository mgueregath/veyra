"""cajas descartadas en las versiones de dataset

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('dataset_items', sa.Column('discarded', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))


def downgrade():
    op.drop_column('dataset_items', 'discarded')
