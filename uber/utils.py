from uber.common import *

class HTTPRedirect(cherrypy.HTTPRedirect):
    def __init__(self, page, *args, **kwargs):
        args = [self.quote(s) for s in args]
        kwargs = {k:self.quote(v) for k,v in kwargs.items()}
        cherrypy.HTTPRedirect.__init__(self, page.format(*args, **kwargs))
        if URL_BASE.startswith('https'):
            self.urls[0] = self.urls[0].replace('http://', 'https://')

    def quote(self, s):
        return quote(s) if isinstance(s, str) else str(s)


def localized_now():
    return EVENT_TIMEZONE.localize(datetime.now())


def comma_and(xs):
    if len(xs) > 1:
        xs[-1] = 'and ' + xs[-1]
    return (', ' if len(xs) > 2 else ' ').join(xs)


def check_csrf(csrf_token):
    if csrf_token is None:
        csrf_token = cherrypy.request.headers.get('CSRF-Token')
    assert csrf_token, 'CSRF token missing'
    if csrf_token != cherrypy.session['csrf_token']:
        log.error("csrf tokens don't match: {!r} != {!r}", csrf_token, cherrypy.session['csrf_token'])
        raise AssertionError('CSRF check failed')
    else:
        cherrypy.request.headers['CSRF-Token'] = csrf_token

def check(model):
    prefix = model.__class__.__name__.lower() + '_'

    for field,name in getattr(model_checks, prefix + 'required', []):
        if not str(getattr(model,field)).strip():
            return name + ' is a required field'

    for name,attr in model_checks.__dict__.items():
        if name.startswith(prefix) and hasattr(attr, '__call__'):
            message = attr(model)
            if message:
                return message


class Order:
    def __init__(self, order):
        self.order = order

    def __getitem__(self, field):
        return ('-' + field) if field==self.order else field

    def __str__(self):
        return self.order


class Registry:
    @classmethod
    def register(cls, slug, kwargs):
        cls.instances[slug] = cls(slug, **kwargs)

class SeasonEvent(Registry):
    instances = OrderedDict()

    def __init__(self, slug, **kwargs):
        assert re.match('^[a-z0-9_]+$', slug), 'Season Event sections must have separated_by_underscore names'
        for opt in ['url', 'location']:
            assert kwargs.get(opt), '{!r} is a required option for Season Event subsections'.format(opt)

        self.slug = slug
        self.name = kwargs['name'] or slug.replace('_', ' ').title()
        self.day = EVENT_TIMEZONE.localize(datetime.strptime(kwargs['day'], '%Y-%m-%d'))
        self.url = kwargs['url']
        self.location = kwargs['location']
        if kwargs['deadline']:
            self.deadline = EVENT_TIMEZONE.localize(datetime.strptime(kwargs['deadline'], '%Y-%m-%d'))
        else:
            self.deadline = (self.day - timedelta(days = 7)).replace(hour=23, minute=59)

class DeptChecklistConf(Registry):
    instances = OrderedDict()

    def __init__(self, slug, description, deadline, name=None, path=None):
        assert re.match('^[a-z0-9_]+$', slug), 'Dept Head checklist item sections must have separated_by_underscore names'
        self.slug, self.description = slug, description
        self.name = name or slug.replace('_', ' ').title()
        self._path = path or '/dept_checklist/form?slug={slug}'
        self.deadline = EVENT_TIMEZONE.localize(datetime.strptime(deadline, '%Y-%m-%d')).replace(hour=23, minute=59)

    def path(self, attendee):
        dept = attendee and attendee.assigned_depts and attendee.assigned_depts_ints[0]
        return self._path.format(slug=self.slug, department=dept)

    def completed(self, attendee):
        matches = [item for item in attendee.dept_checklist_items if self.slug == item.slug]
        return matches[0] if matches else None


for _slug, _conf in SEASON_EVENTS.items():
    SeasonEvent.register(_slug, _conf)

for _slug, _conf in sorted(DEPT_HEAD_CHECKLIST.items(), key=lambda tup: tup[1]['deadline']):
    DeptChecklistConf.register(_slug, _conf)


def hour_day_format(dt):
    return dt.astimezone(EVENT_TIMEZONE).strftime('%I%p ').strip('0').lower() + dt.astimezone(EVENT_TIMEZONE).strftime('%a')


def underscorize(s):
    s = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', s)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s).lower()


def send_email(source, dest, subject, body, format='text', cc=(), bcc=(), model=None):
    subject = subject.format(EVENT_NAME=EVENT_NAME)
    to, cc, bcc = map(listify, [dest, cc, bcc])
    if DEV_BOX:
        for xs in [to, cc, bcc]:
            xs[:] = [email for email in xs if email.endswith('mailinator.com') or DEVELOPER_EMAIL in email]

    if SEND_EMAILS and to:
        message = EmailMessage(subject=subject, **{'bodyText' if format == 'text' else 'bodyHtml': body})
        AmazonSES(AWS_ACCESS_KEY, AWS_SECRET_KEY).sendEmail(
            source = source,
            toAddresses = to,
            ccAddresses = cc,
            bccAddresses = bcc,
            message = message
        )
        sleep(0.1)  # avoid hitting rate limit
    else:
        log.error('email sending turned off, so unable to send {}', locals())

    if model and dest:
        body = body.decode('utf-8') if isinstance(body, bytes) else body
        fk = {'model': 'n/a'} if model == 'n/a' else {'fk_id': model.id, 'model': model.__class__.__name__}
        with Session() as session:
            session.add(Email(subject=subject, dest=','.join(listify(dest)), body=body, **fk))


class Charge:
    def __init__(self, targets=(), amount=None, description=None):
        self.targets = [self.to_sessionized(m) for m in listify(targets)]
        self.amount = amount or self.total_cost
        self.description = description or self.names

    @staticmethod
    def to_sessionized(m):
        if isinstance(m, dict):
            return m
        elif isinstance(m, Attendee):
            return m.to_dict()
        elif isinstance(m, Group):
            return m.to_dict(Group.to_dict_default_attrs + ['attendees'])
        else:
            raise AssertionError('{} is not an attendee or group'.format(m))

    @staticmethod
    def from_sessionized(d):
        assert d['_model'] in {'Attendee', 'Group'}
        if d['_model'] == 'Group':
            d = dict(d, attendees=[Attendee(**a) for a in d.get('attendees', [])])
        return Session.resolve_model(d['_model'])(**d)

    @staticmethod
    def get(payment_id):
        charge = cherrypy.session.pop(payment_id, None)
        if charge:
            return Charge(**charge)
        else:
            raise HTTPRedirect('../preregistration/credit_card_retry')

    def to_dict(self):
        return {
            'targets': self.targets,
            'amount': self.amount,
            'description': self.description
        }

    @property
    def models(self):
        return [self.from_sessionized(d) for d in self.targets]

    @property
    def total_cost(self):
        return 100 * sum(m.amount_unpaid for m in self.models)

    @property
    def dollar_amount(self):
        return self.amount // 100

    @property
    def names(self):
        return ', '.join(getattr(m, 'name', getattr(m, 'full_name', None)) for m in self.models)

    @property
    def attendees(self):
        return [m for m in self.models if isinstance(m, Attendee)]

    @property
    def groups(self):
        return [m for m in self.models if isinstance(m, Group)]

    def charge_cc(self, token):
        try:
            self.response = stripe.Charge.create(
                card=token,
                currency='usd',
                amount=self.amount,
                description=self.description
            )
        except stripe.CardError as e:
            return 'Your card was declined with the following error from our processor: ' + str(e)
        except stripe.StripeError as e:
            log.error('unexpected stripe error', exc_info=True)
            return 'An unexpected problem occured while processing your card: ' + str(e)


def get_page(page, queryset):
    return queryset[(int(page) - 1) * 100 : int(page) * 100]


def genpasswd():
    try:
        with open('/usr/share/dict/words') as f:
            words = [s.strip() for s in f.readlines() if "'" not in s and s.islower() and 3 < len(s) < 8]
            return ' '.join(random.choice(words) for i in range(4))
    except:
        return ''.join(chr(randrange(33, 127)) for i in range(8))

@entry_point
def print_config():
    """
    print all config values to stdout, used for debugging / status checking
    useful if you want to verify that Ubersystem has pulled in the INI values you think it has.
    """
    from uber.config import _config
    pprint(_config.dict())