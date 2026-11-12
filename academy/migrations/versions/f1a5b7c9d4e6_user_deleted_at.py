"""Mark accounts whose owner asked for their details to be erased

Revision ID: f1a5b7c9d4e6
Revises: e0f4a6b8c3d5
Create Date: 2026-11-13 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1a5b7c9d4e6'
down_revision = 'e0f4a6b8c3d5'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user') as batch:
        batch.add_column(sa.Column('deleted_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('user') as batch:
        batch.drop_column('deleted_at')
