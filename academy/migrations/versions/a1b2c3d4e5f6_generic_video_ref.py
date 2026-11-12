"""Generic video reference for hosted video providers

Replaces lesson.vdocipher_id with lesson.video_ref so any hosted provider
(VdoCipher, Bunny Stream, ...) can store its video ID. Existing values are kept.

Revision ID: a1b2c3d4e5f6
Revises: 237cd953ced9
"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = '237cd953ced9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('lesson', schema=None) as batch_op:
        batch_op.add_column(sa.Column('video_ref', sa.String(length=64), nullable=True))
    op.execute("UPDATE lesson SET video_ref = vdocipher_id WHERE vdocipher_id IS NOT NULL")
    with op.batch_alter_table('lesson', schema=None) as batch_op:
        batch_op.drop_column('vdocipher_id')


def downgrade():
    with op.batch_alter_table('lesson', schema=None) as batch_op:
        batch_op.add_column(sa.Column('vdocipher_id', sa.String(length=64), nullable=True))
    op.execute("UPDATE lesson SET vdocipher_id = video_ref WHERE video_provider = 'vdocipher'")
    with op.batch_alter_table('lesson', schema=None) as batch_op:
        batch_op.drop_column('video_ref')
