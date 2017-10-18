import json
from collections import defaultdict
from datetime import date, datetime
from uuid import uuid4

from pytz import UTC
from sideboard.lib import cached_property, listify, log
from sideboard.lib.sa import CoerceUTF8 as UnicodeText, \
    UTCDateTime, UUID
from sqlalchemy import case, func, or_
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import joinedload, backref
from sqlalchemy.schema import ForeignKey, Index, UniqueConstraint
from sqlalchemy.types import Boolean, Integer, Date

from uber.config import c
from uber.custom_tags import safe_string
from uber.decorators import cost_property, predelete_adjustment, \
    presave_adjustment, render
from uber.models import MagModel
from uber.models.group import Group
from uber.models.types import default_relationship as relationship, utcnow, \
    Choice, DefaultColumn as Column, MultiChoice, TakesPaymentMixin
from uber.utils import add_opt, comma_and, get_age_from_birthday, \
    get_real_badge_type, hour_day_format, localized_now, remove_opt, \
    send_email


__all__ = ['Attendee', 'FoodRestrictions']


class Attendee(MagModel, TakesPaymentMixin):
    watchlist_id = Column(
        UUID, ForeignKey('watch_list.id', ondelete='set null'), nullable=True,
        default=None)

    group_id = Column(
        UUID, ForeignKey('group.id', ondelete='SET NULL'), nullable=True)
    group = relationship(
        Group, backref='attendees', foreign_keys=group_id,
        cascade='save-update,merge,refresh-expire,expunge')

    # NOTE: The cascade relationships for promo_code do NOT include
    # "save-update". During the preregistration workflow, before an Attendee
    # has paid, we create ephemeral Attendee objects that are saved in the
    # cherrypy session, but are NOT saved in the database. If the cascade
    # relationships specified "save-update" then the Attendee would
    # automatically be inserted in the database when the promo_code is set on
    # the Attendee object (which we do not want until the attendee pays).
    #
    # The practical result of this is that we must manually set promo_code_id
    # in order for the relationship to be persisted.
    promo_code_id = Column(
        UUID, ForeignKey('promo_code.id'), nullable=True, index=True)
    promo_code = relationship(
        'PromoCode',
        backref=backref('used_by', cascade='merge,refresh-expire,expunge'),
        foreign_keys=promo_code_id,
        cascade='merge,refresh-expire,expunge')

    placeholder = Column(Boolean, default=False, admin_only=True)
    first_name = Column(UnicodeText)
    last_name = Column(UnicodeText)
    legal_name = Column(UnicodeText)
    email = Column(UnicodeText)
    birthdate = Column(Date, nullable=True, default=None)
    age_group = Column(
        Choice(c.AGE_GROUPS), default=c.AGE_UNKNOWN, nullable=True)

    international = Column(Boolean, default=False)
    zip_code = Column(UnicodeText)
    address1 = Column(UnicodeText)
    address2 = Column(UnicodeText)
    city = Column(UnicodeText)
    region = Column(UnicodeText)
    country = Column(UnicodeText)
    no_cellphone = Column(Boolean, default=False)
    ec_name = Column(UnicodeText)
    ec_phone = Column(UnicodeText)
    cellphone = Column(UnicodeText)

    # Represents a request for hotel booking info during preregistration
    requested_hotel_info = Column(Boolean, default=False)

    interests = Column(MultiChoice(c.INTEREST_OPTS))
    found_how = Column(UnicodeText)
    comments = Column(UnicodeText)
    for_review = Column(UnicodeText, admin_only=True)
    admin_notes = Column(UnicodeText, admin_only=True)

    public_id = Column(UUID, default=lambda: str(uuid4()))
    badge_num = Column(Integer, default=None, nullable=True, admin_only=True)
    badge_type = Column(Choice(c.BADGE_OPTS), default=c.ATTENDEE_BADGE)
    badge_status = Column(
        Choice(c.BADGE_STATUS_OPTS), default=c.NEW_STATUS, index=True,
        admin_only=True)
    ribbon = Column(MultiChoice(c.RIBBON_OPTS), admin_only=True)

    affiliate = Column(UnicodeText)

    # attendee shirt size for both swag and staff shirts
    shirt = Column(Choice(c.SHIRT_OPTS), default=c.NO_SHIRT)
    can_spam = Column(Boolean, default=False)
    regdesk_info = Column(UnicodeText, admin_only=True)
    extra_merch = Column(UnicodeText, admin_only=True)
    got_merch = Column(Boolean, default=False, admin_only=True)

    reg_station = Column(Integer, nullable=True, admin_only=True)
    registered = Column(UTCDateTime, server_default=utcnow())
    confirmed = Column(UTCDateTime, nullable=True, default=None)
    checked_in = Column(UTCDateTime, nullable=True)

    paid = Column(
        Choice(c.PAYMENT_OPTS), default=c.NOT_PAID, index=True,
        admin_only=True)
    overridden_price = Column(Integer, nullable=True, admin_only=True)
    base_badge_price = Column(Integer, default=0, admin_only=True)
    amount_paid = Column(Integer, default=0, admin_only=True)
    amount_extra = Column(
        Choice(c.DONATION_TIER_OPTS, allow_unspecified=True), default=0)
    extra_donation = Column(Integer, default=0)
    payment_method = Column(Choice(c.PAYMENT_METHOD_OPTS), nullable=True)
    amount_refunded = Column(Integer, default=0, admin_only=True)

    badge_printed_name = Column(UnicodeText)

    staffing = Column(Boolean, default=False)
    requested_depts = Column(MultiChoice(c.JOB_INTEREST_OPTS))
    assigned_depts = Column(MultiChoice(c.JOB_LOCATION_OPTS), admin_only=True)
    trusted_depts = Column(MultiChoice(c.JOB_LOCATION_OPTS), admin_only=True)
    nonshift_hours = Column(Integer, default=0, admin_only=True)
    past_years = Column(UnicodeText, admin_only=True)
    can_work_setup = Column(Boolean, default=False, admin_only=True)
    can_work_teardown = Column(Boolean, default=False, admin_only=True)

    # TODO: a record of when an attendee is unable to pickup a shirt
    # (which type? swag or staff? prob swag)
    no_shirt = relationship(
        'NoShirt', backref=backref('attendee', load_on_pending=True),
        uselist=False)

    admin_account = relationship(
        'AdminAccount', backref=backref('attendee', load_on_pending=True),
        uselist=False)
    food_restrictions = relationship(
        'FoodRestrictions', backref=backref('attendee', load_on_pending=True),
        uselist=False)

    shifts = relationship('Shift', backref='attendee')
    sales = relationship(
        'Sale', backref='attendee',
        cascade='save-update,merge,refresh-expire,expunge')
    mpoints_for_cash = relationship('MPointsForCash', backref='attendee')
    old_mpoint_exchanges = relationship(
        'OldMPointExchange', backref='attendee')
    dept_checklist_items = relationship(
        'DeptChecklistItem', backref='attendee')

    _attendee_table_args = [Index('ix_attendee_paid_group_id', paid, group_id)]
    if not c.SQLALCHEMY_URL.startswith('sqlite'):
        _attendee_table_args.append(UniqueConstraint(
            'badge_num', deferrable=True, initially='DEFERRED'))

    __table_args__ = tuple(_attendee_table_args)
    _repr_attr_names = ['full_name']

    @predelete_adjustment
    def _shift_badges(self):
        if self.badge_num:
            self.session.shift_badges(
                self.badge_type, self.badge_num + 1, down=True)

    @presave_adjustment
    def _misc_adjustments(self):
        if not self.amount_extra:
            self.affiliate = ''

        if self.birthdate == '':
            self.birthdate = None

        if not self.extra_donation:
            self.extra_donation = 0

        if not self.gets_any_kind_of_shirt:
            self.shirt = c.NO_SHIRT

        if self.paid != c.REFUNDED:
            self.amount_refunded = 0

        if self.badge_cost == 0 and self.paid in [c.NOT_PAID, c.PAID_BY_GROUP]:
            self.paid = c.NEED_NOT_PAY

        if not self.base_badge_price:
            self.base_badge_price = self.new_badge_cost

        if c.AT_THE_CON and self.badge_num and not self.checked_in and \
                self.is_new and \
                self.badge_type not in c.PREASSIGNED_BADGE_TYPES:
            self.checked_in = datetime.now(UTC)

        if self.birthdate:
            self.age_group = self.age_group_conf['val']

        for attr in ['first_name', 'last_name']:
            value = getattr(self, attr)
            if value.isupper() or value.islower():
                setattr(self, attr, value.title())

        if self.legal_name and self.full_name == self.legal_name:
            self.legal_name = ''

    @presave_adjustment
    def _status_adjustments(self):
        if self.badge_status == c.NEW_STATUS and self.banned:
            self.badge_status = c.WATCHED_STATUS
            try:
                send_email(
                    c.SECURITY_EMAIL, [c.REGDESK_EMAIL, c.SECURITY_EMAIL],
                    c.EVENT_NAME + ' WatchList Notification',
                    render('emails/reg_workflow/attendee_watchlist.txt', {
                        'attendee': self}),
                    model='n/a')
            except:
                log.error('unable to send banned email about {}', self)

        elif self.badge_status == c.NEW_STATUS and not self.placeholder and \
                self.first_name and (
                    self.paid in [c.HAS_PAID, c.NEED_NOT_PAY] or
                    self.paid == c.PAID_BY_GROUP and
                    self.group_id and
                    not self.group.is_unpaid):
            self.badge_status = c.COMPLETED_STATUS

    @presave_adjustment
    def _staffing_adjustments(self):
        if c.DEPT_HEAD_RIBBON in self.ribbon_ints:
            self.staffing = True
            if c.SHIFT_CUSTOM_BADGES or \
                    c.STAFF_BADGE not in c.PREASSIGNED_BADGE_TYPES:
                self.badge_type = c.STAFF_BADGE
            if self.paid == c.NOT_PAID:
                self.paid = c.NEED_NOT_PAY
        elif c.VOLUNTEER_RIBBON in self.ribbon_ints and self.is_new:
            self.staffing = True

        if not self.is_new:
            old_ribbon = map(int, self.orig_value_of('ribbon').split(',')) \
                if self.orig_value_of('ribbon') else []
            old_staffing = self.orig_value_of('staffing')

            if self.staffing and not old_staffing or \
                    c.VOLUNTEER_RIBBON in self.ribbon_ints and \
                    c.VOLUNTEER_RIBBON not in old_ribbon:
                self.staffing = True

            elif old_staffing and not self.staffing or \
                    not set([c.VOLUNTEER_RIBBON, c.DEPT_HEAD_RIBBON]) \
                    .intersection(self.ribbon_ints) and \
                    c.VOLUNTEER_RIBBON in old_ribbon:
                self.unset_volunteering()

        if self.badge_type == c.STAFF_BADGE:
            self.ribbon = remove_opt(self.ribbon_ints, c.VOLUNTEER_RIBBON)

        elif self.staffing and self.badge_type != c.STAFF_BADGE and \
                c.VOLUNTEER_RIBBON not in self.ribbon_ints:
            self.ribbon = add_opt(self.ribbon_ints, c.VOLUNTEER_RIBBON)

        if self.badge_type == c.STAFF_BADGE:
            self.staffing = True
            if not self.overridden_price and \
                    self.paid in [c.NOT_PAID, c.PAID_BY_GROUP]:
                self.paid = c.NEED_NOT_PAY

        # remove trusted status from any dept we are not assigned to
        self.trusted_depts = ','.join(
            str(td) for td in self.trusted_depts_ints
            if td in self.assigned_depts_ints)

    @presave_adjustment
    def _badge_adjustments(self):
        from uber.badge_funcs import needs_badge_num
        if self.badge_type == c.PSEUDO_DEALER_BADGE:
            self.ribbon = add_opt(self.ribbon_ints, c.DEALER_RIBBON)

        self.badge_type = self.badge_type_real

        old_type = self.orig_value_of('badge_type')
        old_num = self.orig_value_of('badge_num')

        if not needs_badge_num(self):
            self.badge_num = None

        if old_type != self.badge_type or old_num != self.badge_num:
            self.session.update_badge(self, old_type, old_num)
        elif needs_badge_num(self) and not self.badge_num:
            self.badge_num = self.session.get_next_badge_num(self.badge_type)

    @presave_adjustment
    def _use_promo_code(self):
        if c.BADGE_PROMO_CODES_ENABLED and self.promo_code and \
                not self.overridden_price and self.is_unpaid:
            if self.badge_cost > 0:
                self.overridden_price = self.badge_cost
            else:
                self.paid = c.NEED_NOT_PAY

    def unset_volunteering(self):
        self.staffing = False
        self.trusted_depts = self.requested_depts = self.assigned_depts = ''
        self.ribbon = remove_opt(self.ribbon_ints, c.VOLUNTEER_RIBBON)
        if self.badge_type == c.STAFF_BADGE:
            self.badge_type = c.ATTENDEE_BADGE
            self.badge_num = None
        del self.shifts[:]

    @property
    def ribbon_and_or_badge(self):
        if self.ribbon and self.badge_type != c.ATTENDEE_BADGE:
            return ' / '.join([self.badge_type_label] + self.ribbon_labels)
        elif self.ribbon:
            return ' / '.join(self.ribbon_labels)
        else:
            return self.badge_type_label

    @property
    def badge_type_real(self):
        return get_real_badge_type(self.badge_type)

    @cost_property
    def badge_cost(self):
        return self.calculate_badge_cost()

    @property
    def badge_cost_without_promo_code(self):
        return self.calculate_badge_cost(use_promo_code=False)

    def calculate_badge_cost(self, use_promo_code=True):
        if self.paid == c.NEED_NOT_PAY:
            return 0
        elif self.overridden_price is not None:
            return self.overridden_price
        elif self.base_badge_price:
            cost = self.base_badge_price
        else:
            cost = self.new_badge_cost

        if c.BADGE_PROMO_CODES_ENABLED and self.promo_code and use_promo_code:
            return self.promo_code.calculate_discounted_price(cost)
        else:
            return cost

    @property
    def new_badge_cost(self):
        # What this badge would cost if it were new, i.e., not taking into
        # account special overrides
        registered = self.registered_local if self.registered else None
        if self.is_dealer:
            return c.DEALER_BADGE_PRICE
        elif self.badge_type == c.ONE_DAY_BADGE:
            return c.get_oneday_price(registered)
        elif self.is_presold_oneday:
            return c.get_presold_oneday_price(self.badge_type)
        elif self.badge_type in c.BADGE_TYPE_PRICES:
            return int(c.BADGE_TYPE_PRICES[self.badge_type])
        elif self.age_discount != 0:
            return max(0, c.get_attendee_price(registered) + self.age_discount)
        elif self.group and self.paid == c.PAID_BY_GROUP:
            return c.get_attendee_price(registered) - c.GROUP_DISCOUNT
        else:
            return c.get_attendee_price(registered)

    @property
    def promo_code_code(self):
        """
        Convenience property for accessing `promo_code.code` if available.

        Returns:
            str: `promo_code.code` if `promo_code` is not `None`, empty string
                otherwise.
        """
        return self.promo_code.code if self.promo_code else ''

    @property
    def age_discount(self):
        return -self.age_group_conf['discount']

    @property
    def age_group_conf(self):
        if self.birthdate:
            day = c.EPOCH.date() \
                if date.today() <= c.EPOCH.date() \
                else localized_now().date()

            attendee_age = get_age_from_birthday(self.birthdate, day)
            for val, age_group in c.AGE_GROUP_CONFIGS.items():
                if val != c.AGE_UNKNOWN and \
                        age_group['min_age'] <= attendee_age and \
                        attendee_age <= age_group['max_age']:
                    return age_group

        return c.AGE_GROUP_CONFIGS[int(self.age_group or c.AGE_UNKNOWN)]

    @property
    def total_cost(self):
        return self.default_cost + self.amount_extra

    @property
    def total_donation(self):
        return self.total_cost - self.badge_cost

    @cost_property
    def donation_cost(self):
        return self.extra_donation or 0

    @property
    def amount_unpaid(self):
        if self.paid == c.PAID_BY_GROUP:
            personal_cost = max(0, self.total_cost - self.badge_cost)
        else:
            personal_cost = self.total_cost
        return max(0, personal_cost - self.amount_paid)

    @property
    def is_unpaid(self):
        return self.paid == c.NOT_PAID

    @property
    def is_unassigned(self):
        return not self.first_name

    @property
    def is_dealer(self):
        return c.DEALER_RIBBON in self.ribbon_ints or \
            self.badge_type == c.PSEUDO_DEALER_BADGE or (
                self.group and
                self.group.is_dealer and
                self.paid == c.PAID_BY_GROUP)

    @property
    def is_dept_head(self):
        return c.DEPT_HEAD_RIBBON in self.ribbon_ints

    @property
    def is_presold_oneday(self):
        """
        Returns a boolean indicating whether this is a c.FRIDAY/c.SATURDAY/etc
        badge; see the presell_one_days config option for a full explanation.
        """
        return self.badge_type_label in c.DAYS_OF_WEEK

    @property
    def is_not_ready_to_checkin(self):
        """
        Returns None if we are ready for checkin, otherwise a short error
        message why we can't check them in.
        """
        if self.paid == c.NOT_PAID:
            return "Not paid"

        # When someone claims an unassigned group badge on-site, they first
        # fill out a new registration which is paid-by-group but isn't assigned
        # to a group yet (the admin does that when they check in).
        if self.badge_status != c.COMPLETED_STATUS and not (
                self.badge_status == c.NEW_STATUS and
                self.paid == c.PAID_BY_GROUP and
                not self.group_id):
            return "Badge status"

        if self.is_unassigned:
            return "Badge not assigned"

        if self.is_presold_oneday:
            if self.badge_type_label != localized_now().strftime('%A'):
                return "Wrong day"

        return None

    @property
    # should be OK
    def shirt_size_marked(self):
        return self.shirt not in [c.NO_SHIRT, c.SIZE_UNKNOWN]

    @property
    def is_group_leader(self):
        return self.group and self.id == self.group.leader_id

    @property
    def unassigned_name(self):
        if self.group_id and self.is_unassigned:
            return '[Unassigned {self.badge}]'.format(self=self)

    @hybrid_property
    def full_name(self):
        return self.unassigned_name or \
            '{self.first_name} {self.last_name}'.format(self=self)

    @full_name.expression
    def full_name(cls):
        return case([(
            or_(cls.first_name == None, cls.first_name == ''),  # noqa: E711
            'zzz'
        )], else_=func.lower(cls.first_name + ' ' + cls.last_name))

    @hybrid_property
    def last_first(self):
        return self.unassigned_name or \
            '{self.last_name}, {self.first_name}'.format(self=self)

    @last_first.expression
    def last_first(cls):
        return case([(
            or_(cls.first_name == None, cls.first_name == ''),  # noqa: E711
            'zzz'
        )], else_=func.lower(cls.last_name + ', ' + cls.first_name))

    @hybrid_property
    def normalized_email(self):
        return self.normalize_email(self.email)

    @normalized_email.expression
    def normalized_email(cls):
        return func.replace(func.lower(func.trim(cls.email)), '.', '')

    @classmethod
    def normalize_email(cls, email):
        return email.strip().lower().replace('.', '')

    @property
    def watchlist_guess(self):
        try:
            from uber.models import Session
            with Session() as session:
                watchentries = session.guess_attendee_watchentry(self)
                return [w.to_dict() for w in watchentries]
        except Exception as ex:
            log.warning('Error guessing watchlist entry: {}', ex)
            return None

    @property
    def banned(self):
        return listify(self.watch_list or self.watchlist_guess)

    @property
    def badge(self):
        if self.paid == c.NOT_PAID:
            badge = 'Unpaid ' + self.badge_type_label
        elif self.badge_num:
            badge = '{} #{}'.format(self.badge_type_label, self.badge_num)
        else:
            badge = self.badge_type_label

        if self.ribbon:
            badge += ' ({})'.format(", ".join(self.ribbon_labels))

        return badge

    @property
    def is_transferable(self):
        return not self.is_new and \
            not self.trusted_somewhere and \
            not self.checked_in and \
            self.paid in [c.HAS_PAID, c.PAID_BY_GROUP] and \
            self.badge_type in c.TRANSFERABLE_BADGE_TYPES and \
            not self.admin_account

    @property
    def paid_for_a_swag_shirt(self):
        return self.amount_extra >= c.SHIRT_LEVEL

    @property
    def volunteer_swag_shirt_eligible(self):
        """
        Returns: True if this attendee is eligible for a swag shirt *due to
            their status as a volunteer or staff*. They may additionally be
            eligible for a swag shirt for other reasons too.
        """

        # Some events want to exclude staff badges from getting swag shirts
        # (typically because they are getting staff uniform shirts instead).
        if self.badge_type == c.STAFF_BADGE:
            return c.STAFF_ELIGIBLE_FOR_SWAG_SHIRT
        else:
            return c.VOLUNTEER_RIBBON in self.ribbon_ints

    @property
    def volunteer_swag_shirt_earned(self):
        return self.volunteer_swag_shirt_eligible and (
            not self.takes_shifts or self.worked_hours >= 6)

    @property
    def num_swag_shirts_owed(self):
        swag_shirts = int(self.paid_for_a_swag_shirt)
        volunteer_shirts = int(self.volunteer_swag_shirt_eligible)
        return swag_shirts + volunteer_shirts

    @property
    def gets_staff_shirt(self):
        return self.badge_type == c.STAFF_BADGE

    @property
    def gets_any_kind_of_shirt(self):
        return self.gets_staff_shirt or self.num_swag_shirts_owed > 0

    @property
    def has_personalized_badge(self):
        return self.badge_type in c.PREASSIGNED_BADGE_TYPES

    @property
    def donation_swag(self):
        donation_items = [
            desc for amount, desc in sorted(c.DONATION_TIERS.items())
            if amount and self.amount_extra >= amount]

        extra_donations = \
            ['Extra donation of ${}'.format(self.extra_donation)] \
            if self.extra_donation else []

        return donation_items + extra_donations

    @property
    def merch(self):
        """
        Here is the business logic surrounding shirts:

            - People who kick in enough to get a shirt get a shirt.
            - People with staff badges get a configurable number of staff
              shirts.
            - Volunteers who meet the requirements get a complementary swag
              shirt (NOT a staff shirt).

        """
        merch = self.donation_swag

        if self.volunteer_swag_shirt_eligible:
            shirt = c.DONATION_TIERS[c.SHIRT_LEVEL]
            if self.paid_for_a_swag_shirt:
                shirt = 'a 2nd ' + shirt
            if not self.volunteer_swag_shirt_earned:
                shirt += (
                    ' (this volunteer must work at least 6 hours or '
                    'they will be reported for picking up their shirt)')
            merch.append(shirt)

        if self.gets_staff_shirt:
            staff_shirts = '{} Staff Shirt{}'.format(
                c.SHIRTS_PER_STAFFER, 's' if c.SHIRTS_PER_STAFFER > 1 else '')
            if self.shirt_size_marked:
                staff_shirts += ' [{}]'.format(c.SHIRTS[self.shirt])
            merch.append(staff_shirts)

        if self.staffing:
            merch.append('Staffer Info Packet')

        if self.extra_merch:
            merch.append(self.extra_merch)

        return comma_and(merch)

    @property
    def accoutrements(self):
        stuff = [] \
            if not self.ribbon \
            else ['a ' + s + ' ribbon' for s in self.ribbon_labels]

        if c.WRISTBANDS_ENABLED:
            stuff.append('a {} wristband'.format(
                c.WRISTBAND_COLORS[self.age_group]))
        if self.regdesk_info:
            stuff.append(self.regdesk_info)
        return (' with ' if stuff else '') + comma_and(stuff)

    @property
    def is_single_dept_head(self):
        return self.is_dept_head and len(self.assigned_depts_ints) == 1

    @property
    def multiply_assigned(self):
        return len(self.assigned_depts_ints) > 1

    @property
    def takes_shifts(self):
        return bool(
            self.staffing and
            set(self.assigned_depts_ints) - set(c.SHIFTLESS_DEPTS))

    @property
    def hours(self):
        all_hours = set()
        for shift in self.shifts:
            all_hours.update(shift.job.hours)
        return all_hours

    @property
    def hour_map(self):
        all_hours = {}
        for shift in self.shifts:
            for hour in shift.job.hours:
                all_hours[hour] = shift.job
        return all_hours

    @cached_property
    def possible(self):
        assert self.session, (
            '{}.possible property may only be accessed for jobs attached to a '
            'session'.format(self.__class__.__name__))

        if not self.assigned_depts and not c.AT_THE_CON:
            return []
        else:
            from uber.models.admin import Job
            job_filters = [] if c.AT_THE_CON \
                else [Job.location.in_(self.assigned_depts_ints)]

            job_query = self.session.query(Job).filter(*job_filters).options(
                joinedload(Job.shifts)).order_by(Job.start_time)

            return [
                job for job in job_query
                if job.slots > len(job.shifts) and
                job.no_overlap(self) and
                (job.type != c.SETUP or self.can_work_setup) and
                (job.type != c.TEARDOWN or self.can_work_teardown) and
                (not job.restricted or self.trusted_in(job.location))]

    @property
    def possible_opts(self):
        return [
            (job.id, '({}) [{}] {}'.format(
                hour_day_format(job.start_time), job.location_label, job.name))
            for job in self.possible
            if localized_now() < job.start_time]

    @property
    def possible_and_current(self):
        jobs = [s.job for s in self.shifts]
        for job in jobs:
            job.taken = True
        jobs.extend(self.possible)
        return sorted(jobs, key=lambda j: j.start_time)

    @property
    def worked_shifts(self):
        return [s for s in self.shifts if s.worked == c.SHIFT_WORKED]

    @property
    def weighted_hours(self):
        weighted_hours = sum(s.job.weighted_hours for s in self.shifts)
        return weighted_hours + self.nonshift_hours

    @property
    def worked_hours(self):
        weighted_hours = sum(
            s.job.real_duration * s.job.weight for s in self.worked_shifts)
        return weighted_hours + self.nonshift_hours

    def requested(self, department):
        return department in self.requested_depts_ints

    def assigned_to(self, department):
        return int(department or 0) in self.assigned_depts_ints

    def trusted_in(self, department):
        return int(department or 0) in self.trusted_depts_ints

    @property
    def trusted_somewhere(self):
        """
        :return: True if this Attendee is trusted in at least 1 department
        """
        return len(self.trusted_depts_ints) > 0

    def has_shifts_in(self, department):
        return any(shift.job.location == department for shift in self.shifts)

    @property
    def food_restrictions_filled_out(self):
        return self.food_restrictions if c.STAFF_GET_FOOD else True

    @property
    def shift_prereqs_complete(self):
        return not self.placeholder and \
            self.food_restrictions_filled_out and self.shirt_size_marked

    @property
    def past_years_json(self):
        return json.loads(self.past_years or '[]')

    @property
    def must_contact(self):
        chairs = defaultdict(list)
        for dept, head in c.DEPT_HEAD_OVERRIDES.items():
            chairs[dept].append(head)

        for head in self.session.query(Attendee).filter(
                Attendee.ribbon.contains(c.DEPT_HEAD_RIBBON)).order_by(
                'badge_num'):

            for dept in head.assigned_depts_ints:
                chairs[dept].append(head.full_name)

        locations = [s.job.location for s in self.shifts]
        dept_names = dict(c.JOB_LOCATION_OPTS)

        dept_chairs = {
            '({}) {}'.format(dept_names[dept], ' / '.join(chairs[dept]))
            for dept in locations}

        return safe_string('<br/>'.join(sorted(dept_chairs)))


class FoodRestrictions(MagModel):
    attendee_id = Column(UUID, ForeignKey('attendee.id'), unique=True)
    standard = Column(MultiChoice(c.FOOD_RESTRICTION_OPTS))
    sandwich_pref = Column(MultiChoice(c.SANDWICH_OPTS))
    freeform = Column(UnicodeText)

    def __getattr__(self, name):
        try:
            return super(FoodRestrictions, self).__getattr__(name)
        except AttributeError:
            restriction = getattr(c, name.upper())
            if restriction not in c.FOOD_RESTRICTIONS:
                return MagModel.__getattr__(self, name)
            elif restriction == c.VEGAN and c.VEGAN in self.standard_ints:
                return False
            elif restriction == c.PORK and c.VEGAN in self.standard_ints:
                return True
            else:
                return restriction in self.standard_ints
