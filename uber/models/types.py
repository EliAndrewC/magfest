from collections import Mapping, OrderedDict
from datetime import datetime, time, timedelta

from sideboard.lib import listify
from sideboard.lib.sa import _underscore_to_camelcase as camel, JSON, \
    CoerceUTF8 as UnicodeText, UTCDateTime, UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import relationship
from sqlalchemy.schema import Column
from sqlalchemy.sql.expression import FunctionElement
from sqlalchemy.types import Integer, TypeDecorator

from uber.config import c
from uber.custom_tags import fieldify


__all__ = [
    'default_relationship', 'relationship', 'utcnow', 'Choice', 'Column',
    'DefaultColumn', 'JSONColumnMixin', 'MultiChoice', 'TakesPaymentMixin']


def DefaultColumn(*args, admin_only=False, **kwargs):
    """
    Returns a SQLAlchemy Column with the given parameters, except that instead
    of the regular defaults, we've overridden the following defaults if no
    value is provided for the following parameters:

        Field           Old Default     New Default
        -----           ------------    -----------
        nullable        True            False
        default         None            ''  (only for UnicodeText fields)
        server_default  None            <same value as 'default'>

    We also have an "admin_only" parameter, which is set as an attribute on
    the column instance, indicating whether the column should be settable by
    regular attendees filling out one of the registration forms or if only a
    logged-in admin user should be able to set it.
    """
    kwargs.setdefault('nullable', False)
    type_ = args[0]
    if type_ is UnicodeText or isinstance(type_, (UnicodeText, MultiChoice)):
        kwargs.setdefault('default', '')
    default = kwargs.get('default')
    if isinstance(default, (int, str)):
        kwargs.setdefault('server_default', str(default))
    col = SQLAlchemy_Column(*args, **kwargs)
    col.admin_only = admin_only or type_ in (UUID, UTCDateTime)
    return col


def default_relationship(*args, **kwargs):
    """
    Returns a SQLAlchemy relationship with the given parameters, except that
    instead of the regular defaults, we've overridden the following defaults
    if no value is provided for the following parameters:
        load_on_pending now defaults to True
        cascade now defaults to 'all,delete-orphan'
    """
    kwargs.setdefault('load_on_pending', True)
    kwargs.setdefault('cascade', 'all,delete-orphan')
    return SQLAlchemy_relationship(*args, **kwargs)


# Alias Column and relationship to maintain backwards compatibility
SQLAlchemy_Column, Column = Column, DefaultColumn
SQLAlchemy_relationship, relationship = relationship, default_relationship


class utcnow(FunctionElement):
    """
    We have some tables where we want to save a timestamp on each row
    indicating when the row was first created.  Normally we could do something
    like this::

        created = Column(UTCDateTime, default=lambda: datetime.now(UTC))

    Unfortunately, there are some cases where we instantiate a model and then
    don't save it until sometime later.  This happens when someone registers
    themselves and then doesn't pay until later, since we don't save them to
    the database until they've paid.  Therefore, we use this class so that we
    can set a timestamp based on when the row was inserted rather than when
    the model was instantiated::

        created = Column(UTCDateTime, server_default=utcnow())

    The pg_utcnow and sqlite_utcnow functions below define the implementation
    for postgres and sqlite, and new functions will need to be written if/when
    we decided to support other databases.
    """
    type = UTCDateTime()


@compiles(utcnow, 'postgresql')
def pg_utcnow(element, compiler, **kw):
    return "timezone('utc', current_timestamp)"


@compiles(utcnow, 'sqlite')
def sqlite_utcnow(element, compiler, **kw):
    return "(datetime('now', 'utc'))"


class Choice(TypeDecorator):
    """
    Utility class for storing the results of a dropdown as a database column.
    """
    impl = Integer

    def __init__(self, choices, *, allow_unspecified=False, **kwargs):
        """
        Args:
            choices: an array of tuples, where the first element of each tuple
                is the integer being stored and the second element is a string
                description of the value.
            allow_unspecified: by default, an exception is raised if you try
                to save a row with a value which is not in the choices list
                passed to this class; set this to True if you want to allow
                non-default values.
        """
        self.choices = dict(choices)
        self.allow_unspecified = allow_unspecified
        TypeDecorator.__init__(self, **kwargs)

    def process_bind_param(self, value, dialect):
        if value is not None:
            try:
                assert self.allow_unspecified or int(value) in self.choices
            except:
                raise ValueError('{!r} not a valid option out of {}'.format(
                    value, self.choices))
            else:
                return int(value)


class MultiChoice(TypeDecorator):
    """
    Utility class for storing the results of a group of checkboxes.  Each value
    is represented by an integer, so we store them as a comma-separated string.
    This can be marginally more convenient than a many-to-many table.  Like the
    Choice class, this takes an array of tuples of integers and strings.
    """
    impl = UnicodeText

    def __init__(self, choices, **kwargs):
        self.choices = choices
        self.choices_dict = dict(choices)
        TypeDecorator.__init__(self, **kwargs)

    def process_bind_param(self, value, dialect):
        """
        Our MultiChoice options may be in one of three forms: a single string,
        a single integer, or a list of strings. We want to end up with a single
        comma-separated string. We also want to make sure an object has only
        unique values in its MultiChoice columns. Therefore, we listify() the
        object to make sure it's in list form, we convert it to a set to
        make all the values unique, and we map the values inside it to strings
        before joining them with commas because the join function can't handle
        a list of integers.
        """
        return ','.join(map(str, list(set(listify(value))))) if value else ''


def JSONColumnMixin(column_name, fields, admin_only=False):
    """
    Creates a new mixin class with a JSON column named column_name.

    The newly created mixin class will have a SQLAlchemy JSON Column, named
    `column_name`, along with two other attributes column_name_fields and
    column_name_qualified_fields which describe the fields that the JSON
    Column is expected to hold.

    For example::

        >>> SocialMediaMixin = JSONColumnMixin('social_media', ['Twitter', 'LinkedIn'])
        >>> SocialMediaMixin.social_media
        Column('social_media', JSON(), table=None, nullable=False, default=ColumnDefault({}), server_default=DefaultClause('{}', for_update=False))
        >>> SocialMediaMixin._social_media_fields
        OrderedDict([('twitter', 'Twitter'), ('linked_in', 'LinkedIn')])
        >>> SocialMediaMixin._social_media_qualified_fields
        OrderedDict([('social_media__twitter', 'twitter'), ('social_media__linked_in', 'linked_in')])

    Instances of the newly created mixin class have convenience accessors for
    the attributes defined by `fields`, both directly and using their fully
    qualified forms::

        >>> sm = SocialMediaMixin()
        >>> sm.twitter = 'https://twitter.com/MAGFest'  # Get and set "twitter" directly
        >>> sm.twitter
        'https://twitter.com/MAGFest'
        >>> sm.social_media__twitter  # Get and set qualified "social_media__twitter"
        'https://twitter.com/MAGFest'
        >>> sm.social_media__twitter = '@MAGFest'
        >>> sm.social_media__twitter
        '@MAGFest'
        >>> sm.social_media  # Actual column updated appropriately
        {'linked_in': '', 'twitter': '@MAGFest'}


    Args:
        column_name (str): The name of the column.
        fields (list): A list of field names you expect the column to hold.
            This can be:
              - A single string, if you're only expecting the column to hold a
                single field.
              - A list of strings, which will be treated as the column labels,
                and converted from CamelCase to under_score for the fields.
              - A map of {string: string}, which will be treated as a mapping
                of field names to field labels.

    Returns:
        type: A new mixin class with a JSON column named column_name.

    """  # noqa: E501
    fields_name = '_{}_fields'.format(column_name)
    qualified_fields_name = '_{}_qualified_fields'.format(column_name)
    if isinstance(fields, Mapping):
        fields = OrderedDict([(fieldify(k), v) for k, v in fields.items()])
    else:
        fields = OrderedDict([(fieldify(s), s) for s in listify(fields)])
    qualified_fields = OrderedDict(
        [(column_name + '__' + s, s) for s in fields.keys()])
    column = Column(column_name, JSON, default={}, server_default='{}')
    attrs = {
        column_name: column,
        fields_name: fields,
        qualified_fields_name: qualified_fields
    }

    _Mixin = type(camel(column_name) + 'Mixin', (object,), attrs)

    def _Mixin__init__(self, *args, **kwargs):
        setattr(self, column_name, {})
        for attr in getattr(self.__class__, fields_name).keys():
            setattr(self, attr, kwargs.pop(attr, ''))
        super(_Mixin, self).__init__(*args, **kwargs)
    _Mixin.__init__ = _Mixin__init__

    def _Mixin__declare_last__(cls):
        setattr(getattr(cls, column_name), 'admin_only', admin_only)
        column = cls.__table__.columns.get(column_name)
        setattr(column, 'admin_only', admin_only)
    _Mixin.__declare_last__ = classmethod(_Mixin__declare_last__)

    def _Mixin__unqualify(cls, name):
        if name in getattr(cls, qualified_fields_name):
            return getattr(cls, qualified_fields_name)[name]
        else:
            return name
    _Mixin.unqualify = classmethod(_Mixin__unqualify)

    def _Mixin__getattr__(self, name):
        name = self.unqualify(name)
        if name in getattr(self.__class__, fields_name):
            return getattr(self, column_name).get(name, '')
        else:
            return super(_Mixin, self).__getattr__(name)
    _Mixin.__getattr__ = _Mixin__getattr__

    def _Mixin__setattr__(self, name, value):
        name = self.unqualify(name)
        if name in getattr(self.__class__, fields_name):
            fields = getattr(self, column_name)
            if fields.get(name) != value:
                fields[name] = value
                super(_Mixin, self).__setattr__(column_name, dict(fields))
        else:
            super(_Mixin, self).__setattr__(name, value)
    _Mixin.__setattr__ = _Mixin__setattr__

    return _Mixin


class TakesPaymentMixin(object):
    @property
    def payment_deadline(self):
        return min(
            c.UBER_TAKEDOWN - timedelta(days=2),
            datetime.combine(
                (self.registered + timedelta(days=14)).date(), time(23, 59)))
