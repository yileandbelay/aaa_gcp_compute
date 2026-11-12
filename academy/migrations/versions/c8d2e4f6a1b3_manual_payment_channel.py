"""How a manually recorded payment arrived (cash, Telebirr, bank) and who entered it

Revision ID: c8d2e4f6a1b3
Revises: b7c1d2e3f4a5
Create Date: 2026-11-13 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c8d2e4f6a1b3'
down_revision = 'b7c1d2e3f4a5'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('payment') as batch:
        batch.add_column(sa.Column('channel', sa.String(length=20), nullable=True))
        batch.add_column(sa.Column('recorded_by_id', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_payment_recorded_by', 'user', ['recorded_by_id'], ['id'])


def downgrade():
    with op.batch_alter_table('payment') as batch:
        batch.drop_constraint('fk_payment_recorded_by', type_='foreignkey')
        batch.drop_column('recorded_by_id')
        batch.drop_column('channel')
