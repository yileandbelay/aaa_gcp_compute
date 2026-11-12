"""Optional course sections

Revision ID: a2b6c8d0e5f7
Revises: f1a5b7c9d4e6
Create Date: 2026-11-13 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a2b6c8d0e5f7'
down_revision = 'f1a5b7c9d4e6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('section',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('course_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=160), nullable=False),
    sa.Column('position', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['course_id'], ['course.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('lesson') as batch:
        batch.add_column(sa.Column('section_id', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_lesson_section', 'section', ['section_id'], ['id'])


def downgrade():
    with op.batch_alter_table('lesson') as batch:
        batch.drop_constraint('fk_lesson_section', type_='foreignkey')
        batch.drop_column('section_id')
    op.drop_table('section')
