"""Add many-to-many tables for stripe transactions

Revision ID: e372e4daf771
Revises: 735063d71b57
Create Date: 2018-08-31 20:42:18.905795

"""


# revision identifiers, used by Alembic.
revision = 'e372e4daf771'
down_revision = '735063d71b57'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
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


def upgrade():
    op.create_table('stripe_transaction_group',
    sa.Column('id', residue.UUID(), nullable=False),
    sa.Column('txn_id', residue.UUID(), nullable=False),
    sa.Column('group_id', residue.UUID(), nullable=False),
    sa.Column('share', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['group_id'], ['group.id'], name=op.f('fk_stripe_transaction_group_group_id_group')),
    sa.ForeignKeyConstraint(['txn_id'], ['stripe_transaction.id'], name=op.f('fk_stripe_transaction_group_txn_id_stripe_transaction')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stripe_transaction_group'))
    )
    op.create_table('stripe_transaction_attendee',
    sa.Column('id', residue.UUID(), nullable=False),
    sa.Column('txn_id', residue.UUID(), nullable=False),
    sa.Column('attendee_id', residue.UUID(), nullable=False),
    sa.Column('share', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['attendee_id'], ['attendee.id'], name=op.f('fk_stripe_transaction_attendee_attendee_id_attendee')),
    sa.ForeignKeyConstraint(['txn_id'], ['stripe_transaction.id'], name=op.f('fk_stripe_transaction_attendee_txn_id_stripe_transaction')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stripe_transaction_attendee'))
    )

    op.drop_column('stripe_transaction', 'fk_id')
    op.drop_column('stripe_transaction', 'fk_model')


def downgrade():
    op.add_column('stripe_transaction', sa.Column('fk_model', sa.VARCHAR(), server_default=sa.text("''::character varying"), autoincrement=False, nullable=False))
    op.add_column('stripe_transaction', sa.Column('fk_id', postgresql.UUID(), autoincrement=False, nullable=False))
    op.drop_table('stripe_transaction_attendee')
    op.drop_table('stripe_transaction_group')
