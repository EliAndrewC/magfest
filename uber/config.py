from uber.common import *


class Config:
    """
    We have two types of configuration.  One is the values which come directly from our config file, such
    as the name of our event.  The other is things which depend on the date/time (such as the badge price,
    which can change over time), or whether we've hit our configured attendance cap (which changes based
    on the state of the database).  See the comments in configspec.ini for explanations of the particilar
    options, which are documented there.

    This class has a single global instance called "c" which contains values of either type of config, e.g.
    if you need to check whether dealer registration is open in your code, you'd say c.DEALER_REG_OPEN
    For all of the datetime config options, we also define BEFORE_ and AFTER_ properties, e.g. you can
    check the booleans returned by c.BEFORE_PLACEHOLDER_DEADLINE or c.AFTER_PLACEHOLDER_DEADLINE
    """

    def get_oneday_price(self, dt):
        default = self.DEFAULT_SINGLE_DAY
        return self.BADGE_PRICES['single_day'].get(dt.strftime('%A'), default)

    def get_attendee_price(self, dt):
        price = self.INITIAL_ATTENDEE
        if self.PRICE_BUMPS_ENABLED:
            for day, bumped_price in sorted(self.PRICE_BUMPS.items()):
                if (dt or datetime.now(UTC)) >= day:
                    price = bumped_price
        return price

    def get_group_price(self, dt):
        return self.get_attendee_price(dt) - self.GROUP_DISCOUNT

    @property
    def DEALER_REG_OPEN(self):
        return self.AFTER_DEALER_REG_START and self.BEFORE_DEALER_REG_SHUTDOWN

    @property
    def BADGES_SOLD(self):
        with sa.Session() as session:
            attendees = session.query(sa.Attendee)
            individuals = attendees.filter(or_(sa.Attendee.paid == self.HAS_PAID, sa.Attendee.paid == self.REFUNDED)).count()
            group_badges = attendees.join(sa.Attendee.group).filter(sa.Attendee.paid == self.PAID_BY_GROUP,
                                                                    sa.Group.amount_paid > 0).count()
            return individuals + group_badges

    @property
    def ONEDAY_BADGE_PRICE(self):
        return self.get_oneday_price(sa.localized_now())

    @property
    def BADGE_PRICE(self):
        return self.get_attendee_price(sa.localized_now())

    @property
    def SUPPORTER_BADGE_PRICE(self):
        return self.BADGE_PRICE + self.SUPPORTER_LEVEL

    @property
    def SEASON_BADGE_PRICE(self):
        return self.BADGE_PRICE + self.SEASON_LEVEL

    @property
    def GROUP_PRICE(self):
        return self.get_group_price(sa.localized_now())

    @property
    def PREREG_BADGE_TYPES(self):
        types = [self.ATTENDEE_BADGE, self.PSEUDO_DEALER_BADGE, self.IND_DEALER_BADGE]
        for reg_open, badge_type in [(self.BEFORE_GROUP_PREREG_TAKEDOWN, self.PSEUDO_GROUP_BADGE)]:
            if reg_open:
                types.append(badge_type)
        return types

    @property
    def PREREG_BADGE_DISPLAY(self):
        """
        During pre-registration, attendees may be able to select certain special registration types,
        the most common being 'single attendee' and 'group leader.' This returns a dict with available
        options.
        :return:
        """
        if c.BEFORE_GROUP_PREREG_TAKEDOWN:
            group_description = 'Register a group of ' + str(c.MIN_GROUP_SIZE) + ' or more and save $' + \
                                str(c.GROUP_DISCOUNT) + ' per badge.'
        else:
            group_description = 'The deadline for Group registration has passed, but you can still register as ' + \
                                'a single attendee.'

        base_description = 'A single attendee badge.'

        prereg_badge_types = {}
        prereg_badge_types['single'] = {
            'value': c.ATTENDEE_BADGE,
            'title': 'Single Badge',
            'description': base_description
        }

        if c.GROUPS_ENABLED:
            prereg_badge_types['group'] = {
                'value': c.PSEUDO_GROUP_BADGE,
                'title': 'Group Leader',
                'description': group_description
            }

        return prereg_badge_types

    @property
    def BADGE_DISPLAY_TYPES(self):
        """
        There are several contexts where we want to display different badge types to select:
        - A new attendee registering for the event
        - An attendee claiming a blank badge in an existing group
        - An existing attendee editing their registration

        This property uses the parameters in the URL to figure out the context and return the correct
        badge types to display for an attendee.
        """
        with sa.Session() as session:
            params = cherrypy.lib.httputil.parse_query_string(cherrypy.request.query_string)

            # This isn't ideal, but bypasses cases where "id" refers to a group id.
            try:
                group = session.group(params['group_id']) if 'group_id' in params else None
                attendee = session.attendee(params['id']) if 'id' in params else None
            except:
                group = None
                attendee = None

            if not attendee:
                # Inherits the group's badge type if it's an attendee claiming a badge in a group
                base_badge_name = group.ribbon_and_or_badge if group else c.BADGES.get(c.ATTENDEE_BADGE)
            else:
                base_badge_name = attendee.ribbon_and_or_badge

            donation_prepend = '' if base_badge_name == c.BADGES.get(c.ATTENDEE_BADGE) else base_badge_name + ' / '
            badge_cost = attendee.badge_cost if attendee else c.BADGE_PRICE

            # The base badge is special, so it's given default values and added manually
            base_description = 'Allows access to '+ c.EVENT_NAME_AND_YEAR +' for its duration.'

            badge_types = {}
            badge_types['base'] = {
                'value': c.ATTENDEE_BADGE,
                'title': base_badge_name + ': $' + str(badge_cost),
                'description': base_description
            }

            # The rest of the badges are added in via config
            for name, option in c.BADGE_DISPLAY_CONFIGS.items():
                if int(option['extra']) in c.PREREG_DONATION_TIERS:
                    badge_types[name] = {
                        'value': c.ATTENDEE_BADGE,
                        'title': donation_prepend + option['name'] + ': $' + str(badge_cost + int(option['extra'])),
                        'description': option['description'],
                        'extra': option['extra']
                    }
            return badge_types

    @property
    def PREREG_DONATION_OPTS(self):
        if self.BEFORE_SUPPORTER_DEADLINE and self.SUPPORTER_AVAILABLE:
            return self.DONATION_TIER_OPTS
        else:
            return [(amt, desc) for amt, desc in self.DONATION_TIER_OPTS if amt < self.SUPPORTER_LEVEL]

    @property
    def PREREG_DONATION_TIERS(self):
        return dict(self.PREREG_DONATION_OPTS)

    @property
    def SUPPORTERS_ENABLED(self):
        return self.SUPPORTER_LEVEL in self.PREREG_DONATION_TIERS

    @property
    def SEASON_SUPPORTERS_ENABLED(self):
        return self.SEASON_LEVEL in self.PREREG_DONATION_TIERS

    @property
    def AT_THE_DOOR_BADGE_OPTS(self):
        opts = [(self.ATTENDEE_BADGE, 'Full Weekend Pass (${})'.format(self.BADGE_PRICE))]
        if self.ONE_DAYS_ENABLED:
            opts.append((self.ONE_DAY_BADGE,  'Single Day Pass (${})'.format(self.ONEDAY_BADGE_PRICE)))
        return opts

    @property
    def PREREG_AGE_GROUP_OPTS(self):
        return [opt for opt in self.AGE_GROUP_OPTS if opt[0] != self.AGE_UNKNOWN]

    @property
    def DISPLAY_ONEDAY_BADGES(self):
        return self.ONE_DAYS_ENABLED and days_before(30, self.EPOCH)

    @property
    def AT_OR_POST_CON(self):
        return self.AT_THE_CON or self.POST_CON

    @property
    def PRE_CON(self):
        return not self.AT_OR_POST_CON

    @property
    def CSRF_TOKEN(self):
        return cherrypy.session['csrf_token'] if 'csrf_token' in cherrypy.session else ''

    @property
    def PAGE_PATH(self):
        return cherrypy.request.path_info

    @property
    def PAGE(self):
        return cherrypy.request.path_info.split('/')[-1]

    @property
    def SUPPORTER_COUNT(self):
        with sa.Session() as session:
            attendees = session.query(sa.Attendee)
            individual_supporters = attendees.filter(sa.Attendee.paid.in_([self.HAS_PAID, self.REFUNDED]),
                                                     sa.Attendee.amount_extra >= self.SUPPORTER_LEVEL).count()
            group_supporters = attendees.filter(sa.Attendee.paid == self.PAID_BY_GROUP,
                                                sa.Attendee.amount_extra >= self.SUPPORTER_LEVEL,
                                                sa.Attendee.amount_paid >= self.SUPPORTER_LEVEL).count()
            return individual_supporters + group_supporters

    @classmethod
    def mixin(cls, klass):
        for attr in dir(klass):
            if not attr.startswith('_'):
                setattr(cls, attr, getattr(klass, attr))
        return cls

    def __getattr__(self, name):
        if name.split('_')[0] in ['BEFORE', 'AFTER']:
            date_setting = getattr(c, name.split('_', 1)[1])
            if not date_setting:
                return False
            elif name.startswith('BEFORE_'):
                return sa.localized_now() < date_setting
            else:
                return sa.localized_now() > date_setting
        elif name.startswith('HAS_') and name.endswith('_ACCESS'):
            return getattr(c, name.split('_')[1]) in sa.AdminAccount.access_set()
        elif name.endswith('_AVAILABLE'):
            item_check = name.rsplit('_', 1)[0]
            stock_setting = getattr(self, item_check + '_STOCK', None)
            count_check = getattr(self, item_check + '_COUNT', None)
            if count_check is None:
                return False  # Things with no count are never considered available
            elif stock_setting is None:
                return True  # Defaults to unlimited stock for any stock not configured
            else:
                return count_check < stock_setting
        else:
            raise AttributeError('no such attribute {}'.format(name))

c = Config()

_config = parse_config(__file__)  # outside this module, we use the above c global instead of using this directly

django.conf.settings.configure(**_config['django'].dict())


def _unrepr(d):
    for opt in d:
        val = d[opt]
        if val in ['True', 'False']:
            d[opt] = ast.literal_eval(val)
        elif isinstance(val, str) and val.isdigit():
            d[opt] = int(val)
        elif isinstance(d[opt], dict):
            _unrepr(d[opt])

_unrepr(_config['appconf'])
c.APPCONF = _config['appconf'].dict()

c.BADGE_PRICES = _config['badge_prices']
for _opt, _val in chain(_config.items(), c.BADGE_PRICES.items()):
    if not isinstance(_val, dict):
        setattr(c, _opt.upper(), _val)

c.DATES = {}
c.TIMESTAMP_FORMAT = '%Y-%m-%d %H:%M:%S'
c.DATE_FORMAT = '%Y-%m-%d'
c.EVENT_TIMEZONE = pytz.timezone(c.EVENT_TIMEZONE)
for _opt, _val in _config['dates'].items():
    if not _val:
        _dt = None
    elif ' ' in _val:
        _dt = c.EVENT_TIMEZONE.localize(datetime.strptime(_val, '%Y-%m-%d %H'))
    else:
        _dt = c.EVENT_TIMEZONE.localize(datetime.strptime(_val + ' 23:59', '%Y-%m-%d %H:%M'))
    setattr(c, _opt.upper(), _dt)
    if _dt:
        c.DATES[_opt.upper()] = _dt

c.PRICE_BUMPS = {}
for _opt, _val in c.BADGE_PRICES['attendee'].items():
    c.PRICE_BUMPS[c.EVENT_TIMEZONE.localize(datetime.strptime(_opt, '%Y-%m-%d'))] = _val


def _make_enum(enum_name, section, prices=False):
    opts, lookup, varnames = [], {}, []
    for name, desc in section.items():
        if isinstance(name, int):
            if prices:
                val, desc = desc, name
            else:
                val = name
        else:
            varnames.append(name.upper())
            val = int(sha512(name.upper().encode()).hexdigest()[:7], 16)
            setattr(c, name.upper(),  val)
        opts.append((val, desc))
        lookup[val] = desc

    enum_name = enum_name.upper()
    setattr(c, enum_name + '_OPTS', opts)
    setattr(c, enum_name + '_VARS', varnames)
    setattr(c, enum_name + ('' if enum_name.endswith('S') else 'S'), lookup)


def _is_intstr(s):
    if s and s[0] in ('-', '+'):
        return s[1:].isdigit()
    return s.isdigit()


for _name, _section in _config['enums'].items():
    _make_enum(_name, _section)

for _name, _val in _config['integer_enums'].items():
    if isinstance(_val, int):
        setattr(c, _name.upper(), _val)

for _name, _section in _config['integer_enums'].items():
    if isinstance(_section, dict):
        _interpolated = OrderedDict()
        for _desc, _val in _section.items():
            if _is_intstr(_val):
                key = int(_val)
            else:
                key = getattr(c, _val.upper())

            _interpolated[key] = _desc

        _make_enum(_name, _interpolated, prices=_name.endswith('_price'))

c.BADGE_RANGES = {}
for _badge_type, _range in _config['badge_ranges'].items():
    c.BADGE_RANGES[getattr(c, _badge_type.upper())] = _range

_make_enum('badge_display', OrderedDict([(name, section['name']) for name, section in _config['badge_display'].items()]))
c.BADGE_DISPLAY_CONFIGS = {}
for _name, _section in _config['badge_display'].items():
    _val = getattr(c, _name.upper())
    c.BADGE_DISPLAY_CONFIGS[_val] = dict(_section.dict(), val=_val)

_make_enum('age_group', OrderedDict([(name, section['desc']) for name, section in _config['age_groups'].items()]))
c.AGE_GROUP_CONFIGS = {}
for _name, _section in _config['age_groups'].items():
    _val = getattr(c, _name.upper())
    c.AGE_GROUP_CONFIGS[_val] = dict(_section.dict(), val=_val)

c.TABLE_PRICES = defaultdict(lambda: _config['table_prices']['default_price'],
                             {int(k): v for k, v in _config['table_prices'].items() if k != 'default_price'})

c.SHIFTLESS_DEPTS = {getattr(c, dept.upper()) for dept in c.SHIFTLESS_DEPTS}
c.PREASSIGNED_BADGE_TYPES = [getattr(c, badge_type.upper()) for badge_type in c.PREASSIGNED_BADGE_TYPES]
c.TRANSFERABLE_BADGE_TYPES = [getattr(c, badge_type.upper()) for badge_type in c.TRANSFERABLE_BADGE_TYPES]

c.SEASON_EVENTS = _config['season_events']
c.DEPT_HEAD_CHECKLIST = _config['dept_head_checklist']

c.BADGE_LOCK = RLock()

c.CON_LENGTH = int((c.ESCHATON - c.EPOCH).total_seconds() // 3600)
c.START_TIME_OPTS = [(dt, dt.strftime('%I %p %a')) for dt in (c.EPOCH + timedelta(hours=i) for i in range(c.CON_LENGTH))]
c.DURATION_OPTS = [(i, '%i hour%s' % (i, ('s' if i > 1 else ''))) for i in range(1, 9)]
c.EVENT_START_TIME_OPTS = [(dt, dt.strftime('%I %p %a') if not dt.minute else dt.strftime('%I:%M %a'))
                           for dt in [c.EPOCH + timedelta(minutes=i * 30) for i in range(2 * c.CON_LENGTH)]]
c.EVENT_DURATION_OPTS = [(i, '%.1f hour%s' % (i/2, 's' if i != 2 else '')) for i in range(1, 19)]
c.SETUP_TIME_OPTS = [(dt, dt.strftime('%I %p %a')) for dt in (c.EPOCH - timedelta(days=2) + timedelta(hours=i) for i in range(16))] \
                  + [(dt, dt.strftime('%I %p %a')) for dt in (c.EPOCH - timedelta(days=1) + timedelta(hours=i) for i in range(24))]
c.TEARDOWN_TIME_OPTS = [(dt, dt.strftime('%I %p %a')) for dt in (c.ESCHATON + timedelta(hours=i) for i in range(6))] \
                     + [(dt, dt.strftime('%I %p %a'))
                        for dt in ((c.ESCHATON + timedelta(days=1)).replace(hour=10) + timedelta(hours=i) for i in range(12))]


c.EVENT_NAME_AND_YEAR = c.EVENT_NAME + (' {}'.format(c.YEAR) if c.YEAR else '')
c.EVENT_YEAR = c.EPOCH.strftime('%Y')
c.EVENT_MONTH = c.EPOCH.strftime('%B')
c.EVENT_START_DAY = int(c.EPOCH.strftime('%d')) % 100
c.EVENT_END_DAY = int(c.ESCHATON.strftime('%d')) % 100

c.DAYS = sorted({(dt.strftime('%Y-%m-%d'), dt.strftime('%a')) for dt, desc in c.START_TIME_OPTS})
c.HOURS = ['{:02}'.format(i) for i in range(24)]
c.MINUTES = ['{:02}'.format(i) for i in range(60)]

c.ORDERED_EVENT_LOCS = [loc for loc, desc in c.EVENT_LOCATION_OPTS]
c.EVENT_BOOKED = {'colspan': 0}
c.EVENT_OPEN   = {'colspan': 1}

c.MAX_BADGE = max(xs[1] for xs in c.BADGE_RANGES.values())

c.JOB_PAGE_OPTS = (
    ('index',    'Calendar View'),
    ('signups',  'Signups View'),
    ('staffers', 'Staffer Summary')
)
c.WEIGHT_OPTS = (
    ('1.0', 'x1.0'),
    ('1.5', 'x1.5'),
    ('2.0', 'x2.0'),
    ('2.5', 'x2.5'),
)
c.JOB_DEFAULTS = ['name', 'description', 'duration', 'slots', 'weight', 'restricted', 'extra15']

c.NIGHT_DISPLAY_ORDER = [getattr(c, night.upper()) for night in c.NIGHT_DISPLAY_ORDER]
c.NIGHT_NAMES = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
c.CORE_NIGHTS = []
_day = c.EPOCH
while _day.date() != c.ESCHATON.date():
    c.CORE_NIGHTS.append(getattr(c, _day.strftime('%A').upper()))
    _day += timedelta(days=1)
c.SETUP_NIGHTS = c.NIGHT_DISPLAY_ORDER[:c.NIGHT_DISPLAY_ORDER.index(c.CORE_NIGHTS[0])]
c.TEARDOWN_NIGHTS = c.NIGHT_DISPLAY_ORDER[1 + c.NIGHT_DISPLAY_ORDER.index(c.CORE_NIGHTS[-1]):]

c.PREREG_SHIRT_OPTS = c.SHIRT_OPTS[1:]
c.MERCH_SHIRT_OPTS = [(c.SIZE_UNKNOWN, 'select a size')] + list(c.PREREG_SHIRT_OPTS)
c.DONATION_TIER_OPTS = [(amt, '+ ${}: {}'.format(amt, desc) if amt else desc) for amt, desc in c.DONATION_TIER_OPTS]

c.STORE_ITEM_NAMES = list(c.STORE_PRICES.keys())
c.FEE_ITEM_NAMES = list(c.FEE_PRICES.keys())

c.WRISTBAND_COLORS = defaultdict(lambda: c.DEFAULT_WRISTBAND, c.WRISTBAND_COLORS)

c.SAME_NUMBER_REPEATED = r'^(\d)\1+$'

stripe.api_key = c.STRIPE_SECRET_KEY
