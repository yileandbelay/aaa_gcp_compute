"""Separate student role flag, so one account can be a teacher or admin and a student

Revision ID: b7c1d2e3f4a5
Revises: 9134420c0cb5
Create Date: 2026-11-12 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7c1d2e3f4a5'
down_revision = '9134420c0cb5'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user') as batch:
        batch.add_column(sa.Column('is_student', sa.Boolean(), nullable=False, server_default=sa.true()))
    # Existing teachers and admins keep acting as staff only; an admin can add the student role later.
    op.execute(sa.text('UPDATE "user" SET is_student = :no WHERE role <> :student').bindparams(
        no=False, student='student'))


def downgrade():
    with op.batch_alter_table('user') as batch:
        batch.drop_column('is_student')
