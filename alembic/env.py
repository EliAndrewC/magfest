from __future__ import with_statement

import logging
import sys
from logging.config import fileConfig
from alembic import context

import sideboard
import uber
from sideboard.lib.sa import CoerceUTF8, UTCDateTime, UUID
from uber.migration import version_locations_option
from uber.models import Choice, MultiChoice, Session


logger = logging.getLogger('alembic.env')

# This is the alembic Config object, which provides access to "alembic.ini".
alembic_config = context.config

version_locations = alembic_config.get_main_option('version_locations')
assert version_locations == version_locations_option, (
        'alembic must be run using the sep command:\n'
        'sep alembic {}'.format(' '.join(sys.argv[1:])))

# Interpret the config file for Python logging. This sets up alembic loggers.
if alembic_config.config_file_name:
    fileConfig(alembic_config.config_file_name)

# Add the model's MetaData object here for "autogenerate" support.
target_metadata = Session.BaseClass.metadata


def include_object(object, name, type_, reflected, compare_to):
    """Exclude alembic's own version tables from alembic's consideration."""
    return not name.startswith('alembic_version')


def render_item(type_, obj, autogen_context):
    """Apply custom rendering for selected items."""
    if type_ == 'type':
        if isinstance(obj, Choice):
            return 'sa.Integer()'
        elif isinstance(obj, UTCDateTime):
            return 'sa.DateTime()'
        elif isinstance(obj, (CoerceUTF8, MultiChoice)):
            return 'sa.Unicode()'
        elif isinstance(obj, UUID):
            autogen_context.imports.add(
                'from sqlalchemy.dialects import postgresql')
            return 'postgresql.UUID()'

            # We always want our generated migration files to use the
            # postgresql dialect, because that's what we use in production.
            # If that ever changes, this is is how we'd conditionally
            # render items:
            #
            # if autogen_context.dialect.name == 'postgresql':
            #     autogen_context.imports.add(
            #         'from sqlalchemy.dialects import postgresql')
            #     return 'postgresql.UUID()'
            # else:
            #     return 'sa.Unicode()'

    # Default rendering for other objects
    return False


def process_revision_directives(context, revision, directives):
    """If an empty migration is autogenerated, don't create a script."""
    if alembic_config.cmd_opts.autogenerate:
        script = directives[0]
        if script.upgrade_ops.is_empty():
            directives[:] = []
            logger.info('Nothing to do.')


def run_migrations_offline():
    """Run migrations in "offline" mode.

    This configures the context with just a URL and not an Engine, though an
    Engine is acceptable here as well.  By skipping the Engine creation we
    don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the script output.

    """
    context.configure(
        include_object=include_object,
        url=uber.config.c.SQLALCHEMY_URL,
        target_metadata=target_metadata,
        render_item=render_item,
        literal_binds=True,
        process_revision_directives=process_revision_directives)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in "online" mode.

    In this scenario we need to create an Engine and associate a connection
    with the context.

    """
    connectable = alembic_config.attributes.get('connection', Session.engine)

    with connectable.connect() as connection:
        context.configure(
            include_object=include_object,
            connection=connection,
            target_metadata=target_metadata,
            render_item=render_item,
            process_revision_directives=process_revision_directives)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
