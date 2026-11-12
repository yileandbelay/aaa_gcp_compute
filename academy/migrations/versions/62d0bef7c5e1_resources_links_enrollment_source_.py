"""Resources links, enrollment source, invitations, contact messages

Revision ID: 62d0bef7c5e1
Revises: a1b2c3d4e5f6
Create Date: 2026-11-12 02:56:20.278929

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '62d0bef7c5e1'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('contact_message',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('subject', sa.String(length=200), nullable=True),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.Column('is_read', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('contact_message', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_contact_message_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_contact_message_ip'), ['ip'], unique=False)

    op.create_table('invitation',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('course_id', sa.Integer(), nullable=False),
    sa.Column('invited_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['course_id'], ['course.id'], ),
    sa.ForeignKeyConstraint(['invited_by_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email', 'course_id')
    )
    with op.batch_alter_table('invitation', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_invitation_email'), ['email'], unique=False)

    with op.batch_alter_table('attachment', schema=None) as batch_op:
        batch_op.add_column(sa.Column('url', sa.String(length=500), nullable=True))
        batch_op.alter_column('stored_name',
               existing_type=sa.VARCHAR(length=255),
               nullable=True)

    with op.batch_alter_table('enrollment', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source', sa.String(length=20), server_default='purchase', nullable=False))
        batch_op.add_column(sa.Column('assigned_by_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_enrollment_assigned_by_id_user', 'user', ['assigned_by_id'], ['id'])



def downgrade():
    with op.batch_alter_table('enrollment', schema=None) as batch_op:
        batch_op.drop_constraint('fk_enrollment_assigned_by_id_user', type_='foreignkey')
        batch_op.drop_column('assigned_by_id')
        batch_op.drop_column('source')

    # Links have no stored file, so they cannot exist in the older schema.
    op.execute("DELETE FROM attachment WHERE stored_name IS NULL")
    with op.batch_alter_table('attachment', schema=None) as batch_op:
        batch_op.alter_column('stored_name',
               existing_type=sa.VARCHAR(length=255),
               nullable=False)
        batch_op.drop_column('url')

    with op.batch_alter_table('invitation', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_invitation_email'))

    op.drop_table('invitation')
    with op.batch_alter_table('contact_message', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_contact_message_ip'))
        batch_op.drop_index(batch_op.f('ix_contact_message_created_at'))

    op.drop_table('contact_message')
