"""Optional course cover image

Revision ID: e0f4a6b8c3d5
Revises: d9e3f5a7b2c4
Create Date: 2026-11-13 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e0f4a6b8c3d5'
down_revision = 'd9e3f5a7b2c4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('course') as batch:
        batch.add_column(sa.Column('cover_image', sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table('course') as batch:
        batch.drop_column('cover_image')
