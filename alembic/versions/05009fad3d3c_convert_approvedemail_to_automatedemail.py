"""Convert ApprovedEmail to AutomatedEmail

Revision ID: 05009fad3d3c
Revises: c49ddb5a4845
Create Date: 2018-03-03 17:14:57.044685

"""


# revision identifiers, used by Alembic.
revision = '05009fad3d3c'
down_revision = 'c49ddb5a4845'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy import cast, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import ForeignKey
from sqlalchemy.sql import table
from sqlalchemy.types import String
import residue


try:
    is_sqlite = op.get_context().dialect.name == 'sqlite'
except:
    is_sqlite = False

if is_sqlite:
    op.get_context().connection.execute('PRAGMA foreign_keys=ON;')
    utcnow_server_default = "(datetime('now', 'utc'))"
else:
    utcnow_server_default = "timezone('utc', current_timestamp)"

def sqlite_column_reflect_listener(inspector, table, column_info):
    """Adds parenthesis around SQLite datetime defaults for utcnow."""
    if column_info['default'] == "datetime('now', 'utc')":
        column_info['default'] = utcnow_server_default

sqlite_reflect_kwargs = {
    'listeners': [('column_reflect', sqlite_column_reflect_listener)]
}

# ===========================================================================
# HOWTO: Handle alter statements in SQLite
#
# def upgrade():
#     if is_sqlite:
#         with op.batch_alter_table('table_name', reflect_kwargs=sqlite_reflect_kwargs) as batch_op:
#             batch_op.alter_column('column_name', type_=sa.Unicode(), server_default='', nullable=False)
#     else:
#         op.alter_column('table_name', 'column_name', type_=sa.Unicode(), server_default='', nullable=False)
#
# ===========================================================================

approved_email_table = table(
    'approved_email',
    sa.Column('id', residue.UUID()),
    sa.Column('ident', sa.Unicode()),
)

email_table = table(
    'email',
    sa.Column('id', residue.UUID()),
    sa.Column('ident', sa.Unicode()),
    sa.Column('automated_email_id', residue.UUID(), ForeignKey('automated_email.id')),
)

automated_email_table = table(
    'automated_email',
    sa.Column('id', residue.UUID()),
    sa.Column('ident', sa.Unicode()),
)


def upgrade():
    from sqlalchemy.engine import reflection
    inspector = reflection.Inspector(op.get_bind())
    existing_primarykeys = set([inspector.get_pk_constraint('approved_email')['name']])

    # Delete rows in the approved_email table that have duplicate idents
    connection = op.get_bind()
    concat = func.group_concat if is_sqlite else func.string_agg
    duplicates = connection.execute(
        concat(cast(approved_email_table.c.id, String), ',')
        .select()
        .group_by(approved_email_table.c.ident)
        .having(func.count() > 1)
    )
    for duplicate_ids in duplicates:
        duplicate_ids = sorted(duplicate_ids[0].split(','))
        op.execute(
            approved_email_table.delete().where(
                approved_email_table.c.id.in_(duplicate_ids[1:])
            )
        )

    op.rename_table('approved_email', 'automated_email')
    if is_sqlite:
        with op.batch_alter_table('automated_email', reflect_kwargs=sqlite_reflect_kwargs) as batch_op:
            batch_op.add_column(sa.Column('model', sa.Unicode(), server_default='', nullable=False))
            batch_op.add_column(sa.Column('subject', sa.Unicode(), server_default='', nullable=False))
            batch_op.add_column(sa.Column('body', sa.Unicode(), server_default='', nullable=False))
            batch_op.add_column(sa.Column('sender', sa.Unicode(), server_default='', nullable=False))
            batch_op.add_column(sa.Column('format', sa.Unicode(), server_default='text', nullable=False))
            batch_op.add_column(sa.Column('cc', sa.Unicode(), server_default='', nullable=False))
            batch_op.add_column(sa.Column('bcc', sa.Unicode(), server_default='', nullable=False))
            batch_op.add_column(sa.Column('approved', sa.Boolean(), server_default='False', nullable=False))
            batch_op.add_column(sa.Column('needs_approval', sa.Boolean(), server_default='True', nullable=False))
            batch_op.add_column(sa.Column('unapproved_count', sa.Integer(), server_default='0', nullable=False))
            batch_op.add_column(sa.Column('allow_post_con', sa.Boolean(), server_default='False', nullable=False))
            batch_op.add_column(sa.Column('allow_at_the_con', sa.Boolean(), server_default='False', nullable=False))
            batch_op.add_column(sa.Column('active_after', residue.UTCDateTime(), nullable=True))
            batch_op.add_column(sa.Column('active_before', residue.UTCDateTime(), nullable=True))
            if 'pk_approved_email' in existing_primarykeys:
                batch_op.drop_constraint('pk_approved_email', type_='primary')
            batch_op.create_primary_key(op.f('pk_automated_email'), ['id'])
            batch_op.create_unique_constraint(op.f('uq_automated_email_ident'), ['ident'])
            batch_op.create_index(op.f('ix_automated_email_active_after_active_before'), ['active_after', 'active_before'], unique=False)

    else:
        op.add_column('automated_email', sa.Column('model', sa.Unicode(), server_default='', nullable=False))
        op.add_column('automated_email', sa.Column('subject', sa.Unicode(), server_default='', nullable=False))
        op.add_column('automated_email', sa.Column('body', sa.Unicode(), server_default='', nullable=False))
        op.add_column('automated_email', sa.Column('sender', sa.Unicode(), server_default='', nullable=False))
        op.add_column('automated_email', sa.Column('format', sa.Unicode(), server_default='text', nullable=False))
        op.add_column('automated_email', sa.Column('cc', sa.Unicode(), server_default='', nullable=False))
        op.add_column('automated_email', sa.Column('bcc', sa.Unicode(), server_default='', nullable=False))
        op.add_column('automated_email', sa.Column('approved', sa.Boolean(), server_default='False', nullable=False))
        op.add_column('automated_email', sa.Column('needs_approval', sa.Boolean(), server_default='True', nullable=False))
        op.add_column('automated_email', sa.Column('unapproved_count', sa.Integer(), server_default='0', nullable=False))
        op.add_column('automated_email', sa.Column('allow_post_con', sa.Boolean(), server_default='False', nullable=False))
        op.add_column('automated_email', sa.Column('allow_at_the_con', sa.Boolean(), server_default='False', nullable=False))
        op.add_column('automated_email', sa.Column('active_after', residue.UTCDateTime(), nullable=True))
        op.add_column('automated_email', sa.Column('active_before', residue.UTCDateTime(), nullable=True))
        op.drop_constraint('pk_approved_email', 'automated_email', type_='primary')
        op.create_primary_key(op.f('pk_automated_email'), 'automated_email', ['id'])
        op.create_unique_constraint(op.f('uq_automated_email_ident'), 'automated_email', ['ident'])


    if is_sqlite:
        with op.batch_alter_table('email', reflect_kwargs=sqlite_reflect_kwargs) as batch_op:
            batch_op.alter_column('dest', new_column_name='to')
            batch_op.add_column(sa.Column('bcc', sa.Unicode(), server_default='', nullable=False))
            batch_op.add_column(sa.Column('cc', sa.Unicode(), server_default='', nullable=False))
            batch_op.add_column(sa.Column('automated_email_id', residue.UUID(), nullable=True))
            batch_op.add_column(sa.Column('sender', sa.Unicode(), server_default='', nullable=False))
            batch_op.create_foreign_key(op.f('fk_email_automated_email_id_automated_email'), 'automated_email', ['automated_email_id'], ['id'], ondelete='set null')
    else:
        op.alter_column('email', 'dest', new_column_name='to')
        op.add_column('email', sa.Column('bcc', sa.Unicode(), server_default='', nullable=False))
        op.add_column('email', sa.Column('cc', sa.Unicode(), server_default='', nullable=False))
        op.add_column('email', sa.Column('automated_email_id', residue.UUID(), nullable=True))
        op.add_column('email', sa.Column('sender', sa.Unicode(), server_default='', nullable=False))
        op.create_foreign_key(op.f('fk_email_automated_email_id_automated_email'), 'email', 'automated_email', ['automated_email_id'], ['id'], ondelete='set null')

    # Use the ident to try to establish a foreign key relationship
    # between the email and automated_email tables
    emails = connection.execute(email_table.select())
    for email in emails:
        if email.ident:
            automated_email = connection.execute(
                automated_email_table.select().where(automated_email_table.c.ident == email.ident)
            ).fetchone()
            if automated_email:
                connection.execute(
                    email_table.update().where(email_table.c.id == email.id).values({'automated_email_id': automated_email.id})
                )


def downgrade():
    op.drop_constraint(op.f('fk_email_automated_email_id_automated_email'), 'email', type_='foreignkey')
    op.alter_column('email', 'to', new_column_name='dest')
    op.drop_column('email', 'sender')
    op.drop_column('email', 'automated_email_id')
    op.drop_column('email', 'cc')
    op.drop_column('email', 'bcc')

    op.drop_column('automated_email', 'model')
    op.drop_column('automated_email', 'subject')
    op.drop_column('automated_email', 'body')
    op.drop_column('automated_email', 'format')
    op.drop_column('automated_email', 'sender')
    op.drop_column('automated_email', 'cc')
    op.drop_column('automated_email', 'bcc')
    op.drop_column('automated_email', 'approved')
    op.drop_column('automated_email', 'needs_approval')
    op.drop_column('automated_email', 'unapproved_count')
    op.drop_column('automated_email', 'allow_post_con')
    op.drop_column('automated_email', 'allow_at_the_con')
    op.drop_column('automated_email', 'active_after')
    op.drop_column('automated_email', 'active_before')
    op.drop_constraint('pk_automated_email', 'automated_email', type_='primary')
    op.create_primary_key(op.f('pk_approved_email'), 'automated_email', ['id'])
    op.drop_constraint('uq_automated_email_ident', 'automated_email', type_='unique')
    op.rename_table('automated_email', 'approved_email')
