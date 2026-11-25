"""Log of every email the site tries to send (to find out why an email did not arrive)

Revision ID: b3c7d9e1f2a4
Revises: a2b6c8d0e5f7
Create Date: 2026-11-24 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3c7d9e1f2a4'
down_revision = 'a2b6c8d0e5f7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('email_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('to_email', sa.String(length=255), nullable=False),
    sa.Column('subject', sa.String(length=300), nullable=True),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('error', sa.String(length=500), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('email_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_email_log_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_email_log_to_email'), ['to_email'], unique=False)


def downgrade():
    with op.batch_alter_table('email_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_email_log_to_email'))
        batch_op.drop_index(batch_op.f('ix_email_log_created_at'))
    op.drop_table('email_log')
