"""Course page sections and reviews

Revision ID: 9134420c0cb5
Revises: 62d0bef7c5e1
Create Date: 2026-11-12 03:07:01.603642

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9134420c0cb5'
down_revision = '62d0bef7c5e1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('review',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('course_id', sa.Integer(), nullable=False),
    sa.Column('rating', sa.Integer(), nullable=False),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('is_hidden', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['course_id'], ['course.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'course_id')
    )
    with op.batch_alter_table('course', schema=None) as batch_op:
        batch_op.add_column(sa.Column('learn_points', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('audience', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('prerequisites', sa.Text(), nullable=True))



def downgrade():
    with op.batch_alter_table('course', schema=None) as batch_op:
        batch_op.drop_column('prerequisites')
        batch_op.drop_column('audience')
        batch_op.drop_column('learn_points')

    op.drop_table('review')
