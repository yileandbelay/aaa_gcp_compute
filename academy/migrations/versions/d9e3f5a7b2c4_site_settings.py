"""Admin-editable site settings (payment instructions)

Revision ID: d9e3f5a7b2c4
Revises: c8d2e4f6a1b3
Create Date: 2026-11-13 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd9e3f5a7b2c4'
down_revision = 'c8d2e4f6a1b3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('site_setting',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('key')
    )


def downgrade():
    op.drop_table('site_setting')
