"""
When an admin submits a form to create/edit an attendee/group/job/etc we usually want to perform some basic validations
on the data that was entered.  We put those validations here.  To make a validation for the Attendee model, you can
just write a function decorated with the @validation.Attendee decorator.  That function should return None on success
and an error string on failure.

In addition, you can define a set of required fields by setting the .required field like the AdminAccount.required list
below.  This should be a list of tuples where the first tuple element is the name of the field, and the second is the
name that should be displayed in the "XXX is a required field" error message.

To perform these validations, call the "check" method on the instance you're validating.  That method returns None
on success and a string error message on validation failure.
"""
import re
from datetime import date
from functools import wraps
from urllib.request import urlopen

import cherrypy
import phonenumbers
from email_validator import validate_email, EmailNotValidError
from pockets.autolog import log

from uber.config import c
from uber.decorators import prereg_validation, validation
from uber.models import AdminAccount, ApiToken, Attendee, AttendeeTournament, Attraction, AttractionFeature, \
    Department, DeptRole, Event, Group, IndieDeveloper, IndieGame, IndieGameCode, IndieJudge, IndieStudio, Job, \
    MITSApplicant, MITSDocument, MITSGame, MITSPicture, MITSTeam, PanelApplicant, PanelApplication, PromoCode, \
    Sale, Session
from uber.utils import localized_now, Charge


AdminAccount.required = [
    ('attendee', 'Attendee'),
    ('hashed', 'Password')
]


@validation.AdminAccount
def duplicate_admin(account):
    if account.is_new:
        with Session() as session:
            if session.query(AdminAccount).filter_by(attendee_id=account.attendee_id).all():
                return 'That attendee already has an admin account'


@validation.AdminAccount
def has_email_address(account):
    if account.is_new:
        with Session() as session:
            if session.query(Attendee).filter_by(id=account.attendee_id).first().email == '':
                return "Attendee doesn't have a valid email set"


@validation.AdminAccount
def admin_has_required_access(account):
    new_access = set(int(s) for s in account.access.split(',') if s)
    old_access = set() if account.is_new else set(int(s) for s in account.orig_value_of('access').split(',') if s)
    access_changes = new_access.symmetric_difference(old_access)
    if any(c.REQUIRED_ACCESS[a] for a in access_changes):
        with Session() as session:
            admin_account = session.current_admin_account()
            admin_access = set(admin_account.access_ints)
            for access_change in access_changes:
                required_access = c.REQUIRED_ACCESS[access_change]
                if all(a not in admin_access for a in required_access):
                    return 'You do not have permission to change that access setting'


ApiToken.required = [('name', 'Name'), ('description', 'Intended Usage'), ('access', 'Access Controls')]


@validation.ApiToken
def admin_has_required_api_access(api_token):
    admin_account_id = cherrypy.session['account_id']
    if api_token.is_new and admin_account_id != api_token.admin_account_id:
            return 'You may not create an API token for another user'

    with Session() as session:
        admin_account = session.current_admin_account()
        token_access = set(api_token.access_ints)
        admin_access = set(admin_account.access_ints)
        if not token_access.issubset(admin_access):
            return 'You do not have permission to create a token with that access'


Group.required = [
    ('name', 'Group Name')
]


@prereg_validation.Group
def dealer_wares(group):
    if group.is_dealer and not group.wares:
        return "You must provide a detailed explanation of what you sell for us to evaluate your submission"


@prereg_validation.Group
def dealer_website(group):
    if group.is_dealer and not group.website:
        return "Please enter your business' website address"


@prereg_validation.Group
def dealer_description(group):
    if group.is_dealer and not group.description:
        return "Please provide a brief description of your business"


@prereg_validation.Group
def dealer_categories(group):
    if group.is_dealer and not group.categories:
        return "Please select at least one category your wares fall under."


@prereg_validation.Group
def dealer_other_category(group):
    if group.categories and c.OTHER in group.categories_ints and not group.categories_text:
        return "Please describe what 'other' categories your wares fall under."


@prereg_validation.Group
def dealer_address(group):
    if group.is_dealer:
        missing = []
        if not group.country:
            missing.append('country')
        if not group.address1:
            missing.append('street address')
        if not group.city:
            missing.append('city')
        if group.country == 'United States':
            if not group.region:
                missing.append('state')
            if not group.zip_code:
                missing.append('zip code')
        if group.country == 'Canada' and not group.region:
            missing.append('province or region')

        if missing:
            return 'Please provide your full address for tax purposes. Missing: {}'.format(', '.join(missing))


@validation.Group
def group_money(group):
    if not group.auto_recalc:
        try:
            cost = int(float(group.cost if group.cost else 0))
            if cost < 0:
                return 'Total Group Price must be a number that is 0 or higher.'
        except Exception:
            return "What you entered for Total Group Price ({}) isn't even a number".format(group.cost)

    try:
        amount_paid = int(float(group.amount_paid if group.amount_paid else 0))
        if amount_paid < 0:
            return 'Amount Paid must be a number that is 0 or higher.'
    except Exception:
        return "What you entered for Amount Paid ({}) isn't even a number".format(group.amount_paid)

    try:
        amount_refunded = int(float(group.amount_refunded if group.amount_refunded else 0))
        if amount_refunded < 0:
            return 'Amount Refunded must be positive'
        elif amount_refunded > amount_paid:
            return 'Amount Refunded cannot be greater than Amount Paid'
    except Exception:
        return "What you entered for Amount Refunded ({}) wasn't even a number".format(group.amount_refunded)


def _invalid_phone_number(s):
    try:
        # parse input as a US number, unless a leading + is provided,
        # in which case the input will be validated according to the country code
        parsed = phonenumbers.parse(s, 'US')
    except phonenumbers.phonenumberutil.NumberParseException:
        # could not be parsed due to unexpected characters
        return True

    if not phonenumbers.is_possible_number(parsed):
        # could not be a phone number due to length, invalid characters, etc
        return True
    elif parsed.country_code == 1 and phonenumbers.length_of_national_destination_code(parsed) == 0:
        # US number does not contain area code
        return True

    return False


def _invalid_zip_code(s):
    return len(re.findall(r'\d', s)) not in [5, 9]


def ignore_unassigned_and_placeholders(func):
    @wraps(func)
    def with_skipping(attendee):
        unassigned_group_reg = attendee.group_id and not attendee.first_name and not attendee.last_name
        valid_placeholder = attendee.placeholder and attendee.first_name and attendee.last_name
        if not unassigned_group_reg and not valid_placeholder:
            return func(attendee)
    return with_skipping


@prereg_validation.Attendee
def shirt_size(attendee):
    if attendee.amount_extra >= c.SHIRT_LEVEL and attendee.shirt == c.NO_SHIRT:
        return 'Your shirt size is required'


@prereg_validation.Attendee
def group_leader_under_13(attendee):
    if attendee.badge_type == c.PSEUDO_GROUP_BADGE and attendee.age_group_conf['val'] in [c.UNDER_6, c.UNDER_13]:
        return "Children under 13 cannot be group leaders."


@prereg_validation.Attendee
def total_cost_over_paid(attendee):
    if attendee.total_cost < attendee.amount_paid:
        if (not attendee.orig_value_of('birthdate') or attendee.orig_value_of('birthdate') < attendee.birthdate) \
                and attendee.age_group_conf['val'] in [c.UNDER_6, c.UNDER_13]:
            return 'The date of birth you entered incurs a discount; ' \
                'please email {} to change your badge and receive a refund'.format(c.REGDESK_EMAIL)
        return 'You have already paid ${}, you cannot reduce your extras below that.'.format(attendee.amount_paid)


@validation.Attendee
def reasonable_total_cost(attendee):
    if attendee.total_cost >= 999999:
        return 'We cannot charge ${:,.2f}. Please reduce extras so the total is below $999,999.'.format(
            attendee.total_cost)


@prereg_validation.Attendee
def promo_code_is_useful(attendee):
    if attendee.promo_code:
        if not attendee.is_unpaid:
            return "You can't apply a promo code after you've paid or if you're in a group."
        elif attendee.overridden_price:
            return "You already have a special badge price, you can't use a promo code on top of that."
        elif attendee.badge_cost >= attendee.badge_cost_without_promo_code:
            return "That promo code doesn't make your badge any cheaper. You may already have other discounts."


@prereg_validation.Attendee
def promo_code_not_is_expired(attendee):
    if attendee.promo_code and attendee.promo_code.is_expired:
        return 'That promo code is expired.'


@prereg_validation.Attendee
def promo_code_has_uses_remaining(attendee):
    if attendee.promo_code and not attendee.promo_code.is_unlimited:
        unpaid_uses_count = Charge.get_unpaid_promo_code_uses_count(
            attendee.promo_code.id, attendee.id)
        if (attendee.promo_code.uses_remaining - unpaid_uses_count) < 0:
            return 'That promo code has been used too many times.'


@validation.Attendee
@ignore_unassigned_and_placeholders
def full_name(attendee):
    if not attendee.first_name:
        return 'First Name is a required field'
    elif not attendee.last_name:
        return 'Last Name is a required field'


@validation.Attendee
def allowed_to_volunteer(attendee):
    if attendee.staffing \
            and not attendee.age_group_conf['can_volunteer'] \
            and attendee.badge_type != c.STAFF_BADGE \
            and c.PRE_CON:

        return 'Your interest is appreciated, but ' + c.EVENT_NAME + ' volunteers must be 18 or older.'


@validation.Attendee
@ignore_unassigned_and_placeholders
def age(attendee):
    if c.COLLECT_EXACT_BIRTHDATE:
        if not attendee.birthdate:
            return 'Please enter a date of birth.'
        elif attendee.birthdate > date.today():
            return 'You cannot be born in the future.'
    elif not attendee.age_group:
        return 'Please enter your age group'


@validation.Attendee
def allowed_to_register(attendee):
    if not attendee.age_group_conf['can_register']:
        return 'Attendees {} years of age do not need to register, ' \
            'but MUST be accompanied by a parent at all times!'.format(attendee.age_group_conf['desc'])


@validation.Attendee
@ignore_unassigned_and_placeholders
def email(attendee):
    if len(attendee.email) > 255:
        return 'Email addresses cannot be longer than 255 characters.'
    elif not attendee.email and not c.AT_OR_POST_CON:
        return 'Please enter an email address.'


@validation.Attendee
def attendee_email_valid(attendee):
    if attendee.email:
        try:
            validate_email(attendee.email)
        except EmailNotValidError as e:
            message = str(e)
            return 'Enter a valid email address. ' + message


@validation.Attendee
@ignore_unassigned_and_placeholders
def address(attendee):
    if c.COLLECT_FULL_ADDRESS:
        if not attendee.address1:
            return 'Please enter a street address.'
        if not attendee.city:
            return 'Please enter a city.'
        if not attendee.region and attendee.country in ['United States', 'Canada']:
            return 'Please enter a state, province, or region.'
        if not attendee.country:
            return 'Please enter a country.'


@validation.Attendee
@ignore_unassigned_and_placeholders
def zip_code(attendee):
    if not attendee.international and not c.AT_OR_POST_CON:
        if _invalid_zip_code(attendee.zip_code):
            return 'Enter a valid zip code'


@validation.Attendee
@ignore_unassigned_and_placeholders
def emergency_contact(attendee):
    if not attendee.ec_name:
        return 'Please tell us the name of your emergency contact.'
    if not attendee.international and _invalid_phone_number(attendee.ec_phone):
        if c.COLLECT_FULL_ADDRESS:
            return 'Enter a 10-digit US phone number or include a ' \
                'country code (e.g. +44) for your emergency contact number.'
        else:
            return 'Enter a 10-digit emergency contact number.'


@validation.Attendee
@ignore_unassigned_and_placeholders
def cellphone(attendee):
    if attendee.cellphone and _invalid_phone_number(attendee.cellphone):
        # phone number was inputted incorrectly
        return 'Your phone number was not a valid 10-digit US phone number. ' \
            'Please include a country code (e.g. +44) for international numbers.'

    if not attendee.no_cellphone and attendee.staffing and not attendee.cellphone:
        return "Phone number is required for volunteers (unless you don't own a cellphone)"


@prereg_validation.Attendee
def dealer_cellphone(attendee):
    if attendee.badge_type == c.PSEUDO_DEALER_BADGE and not attendee.cellphone:
        return 'Your phone number is required'


@validation.Attendee
@ignore_unassigned_and_placeholders
def emergency_contact_not_cellphone(attendee):
    if not attendee.international and attendee.cellphone and attendee.cellphone == attendee.ec_phone:
        return "Your phone number cannot be the same as your emergency contact number"


@validation.Attendee
def printed_badge_change(attendee):
    if attendee.badge_printed_name != attendee.orig_value_of('badge_printed_name') \
            and not AdminAccount.admin_name() \
            and localized_now() > c.get_printed_badge_deadline_by_type(attendee.badge_type):

        return '{} badges have already been ordered, so you cannot change the badge printed name.'.format(
            attendee.badge_type_label if attendee.badge_type in c.PREASSIGNED_BADGE_TYPES else "Supporter")


@validation.Attendee
def group_leadership(attendee):
    if attendee.session and not attendee.group_id:
        orig_group_id = attendee.orig_value_of('group_id')
        if orig_group_id and attendee.id == attendee.session.group(orig_group_id).leader_id:
            return 'You cannot remove the leader of a group from that group; make someone else the leader first'


@validation.Attendee
def banned_volunteer(attendee):
    if (c.VOLUNTEER_RIBBON in attendee.ribbon_ints or attendee.staffing) and attendee.full_name in c.BANNED_STAFFERS:
        return "We've declined to invite {} back as a volunteer, ".format(attendee.full_name) + (
                    'talk to Stops to override if necessary' if c.AT_THE_CON else
                    'Please contact us via {} if you believe this is in error'.format(c.CONTACT_URL))


@validation.Attendee
def attendee_money(attendee):
    try:
        amount_paid = int(float(attendee.amount_paid))
        if amount_paid < 0:
            return 'Amount Paid cannot be less than zero'
    except Exception:
        return "What you entered for Amount Paid ({}) wasn't even a number".format(attendee.amount_paid)

    try:
        amount_extra = int(float(attendee.amount_extra or 0))
        if amount_extra < 0:
            return 'Amount extra must be a positive integer'
    except Exception:
        return 'Invalid amount extra ({})'.format(attendee.amount_extra)

    if attendee.overridden_price is not None:
        try:
            overridden_price = int(float(attendee.overridden_price))
            if overridden_price < 0:
                return 'Overridden price must be a positive integer'
        except Exception:
            return 'Invalid overridden price ({})'.format(attendee.overridden_price)

    try:
        amount_refunded = int(float(attendee.amount_refunded))
        if amount_refunded < 0:
            return 'Amount Refunded must be positive'
        elif amount_refunded > amount_paid:
            return 'Amount Refunded cannot be greater than Amount Paid'
        elif attendee.paid == c.REFUNDED and amount_refunded == 0:
            return 'Amount Refunded may not be 0 if the attendee is marked Paid and Refunded'
    except Exception:
        return "What you entered for Amount Refunded ({}) wasn't even a number".format(attendee.amount_refunded)


@validation.Attendee
def dealer_needs_group(attendee):
    if attendee.is_dealer and not attendee.badge_type == c.PSEUDO_DEALER_BADGE and not attendee.group_id:
        return 'Dealers must be associated with a group'


@validation.Attendee
def dupe_badge_num(attendee):
    if (attendee.badge_num != attendee.orig_value_of('badge_num') or attendee.is_new) \
            and c.NUMBERED_BADGES and attendee.badge_num \
            and (not c.SHIFT_CUSTOM_BADGES or c.AFTER_PRINTED_BADGE_DEADLINE or c.AT_THE_CON):
        with Session() as session:
            existing = session.query(Attendee).filter_by(badge_type=attendee.badge_type, badge_num=attendee.badge_num)
            if existing.count():
                return 'That badge number already belongs to {!r}'.format(existing.first().full_name)


@validation.Attendee
def invalid_badge_num(attendee):
    if c.NUMBERED_BADGES and attendee.badge_num:
        try:
            assert int(attendee.badge_num) is not None
        except Exception:
            return '{!r} is not a valid badge number'.format(attendee.badge_num)


@validation.Attendee
def no_more_custom_badges(attendee):
    if (attendee.badge_type != attendee.orig_value_of('badge_type') or attendee.is_new) \
            and attendee.has_personalized_badge and c.AFTER_PRINTED_BADGE_DEADLINE:
        with Session() as session:
            if all(not session.admin_attendee().is_dept_head_of(d) for d in [c.REGDESK, c.STOPS]):
                return 'Custom badges have already been ordered so you cannot use this badge type'


@validation.Attendee
def out_of_badge_type(attendee):
    if attendee.badge_type != attendee.orig_value_of('badge_type'):
        with Session() as session:
            try:
                session.get_next_badge_num(attendee.badge_type_real)
            except AssertionError:
                return 'There are no more badges available for that type'


@validation.Attendee
def invalid_badge_name(attendee):
    if attendee.badge_printed_name and localized_now() <= c.get_printed_badge_deadline_by_type(attendee.badge_type) \
            and re.search(c.INVALID_BADGE_PRINTED_CHARS, attendee.badge_printed_name):
        return 'Your printed badge name has invalid characters. Please use only printable ASCII characters.'


@validation.Attendee
def extra_donation_valid(attendee):
    try:
        extra_donation = int(float(attendee.extra_donation if attendee.extra_donation else 0))
        if extra_donation < 0:
            return 'Extra Donation must be a number that is 0 or higher.'
    except Exception:
        return "What you entered for Extra Donation ({}) isn't even a number".format(attendee.extra_donation)


@validation.MPointsForCash
@validation.OldMPointExchange
def money_amount(model):
    if not str(model.amount).isdigit():
        return 'Amount must be a positive number'


Job.required = [
    ('name', 'Job Name')
]


@validation.Job
def slots(job):
    if job.slots < len(job.shifts):
        return 'You cannot reduce the number of slots to below the number of staffers currently signed up for this job'


@validation.Job
def time_conflicts(job):
    if not job.is_new:
        original_hours = Job(start_time=job.orig_value_of('start_time'), duration=job.orig_value_of('duration')).hours
        for shift in job.shifts:
            if job.hours.intersection(shift.attendee.hours - original_hours):
                return 'You cannot change this job to this time, because {} is already working a shift then'.format(
                    shift.attendee.full_name)


Department.required = [('name', 'Name'), ('description', 'Description')]
DeptRole.required = [('name', 'Name')]


@validation.DeptChecklistItem
def is_checklist_admin(dept_checklist_item):
    with Session() as session:
        attendee = session.admin_attendee()
        department_id = dept_checklist_item.department_id or dept_checklist_item.department.id
        if not attendee.can_admin_checklist_for(department_id):
            return 'Only checklist admins can complete checklist items'


@validation.OldMPointExchange
def oldmpointexchange_numbers(mpe):
    if not str(mpe.amount).isdigit():
        return 'MPoints must be a positive integer'


Sale.required = [
    ('what', "What's being sold")
]


@validation.Sale
def cash_and_mpoints(sale):
    if not str(sale.cash).isdigit() or int(sale.cash) < 0:
        return 'Cash must be a positive integer'
    if not str(sale.mpoints).isdigit() or int(sale.mpoints) < 0:
        return 'MPoints must be a positive integer'


PromoCode.required = [
    ('expiration_date', 'Expiration date')
]


@validation.PromoCode
def valid_discount(promo_code):
    if promo_code.discount:
        try:
            promo_code.discount = int(promo_code.discount)
            if promo_code.discount < 0:
                return 'You cannot give out promo codes that increase badge prices.'
        except Exception:
            return "What you entered for the discount isn't even a number."


@validation.PromoCode
def valid_uses_allowed(promo_code):
    if promo_code.uses_allowed:
        try:
            promo_code.uses_allowed = int(promo_code.uses_allowed)
            if promo_code.uses_allowed < 0 or promo_code.uses_allowed < promo_code.uses_count:
                return 'Promo codes must have at least 0 uses remaining.'
        except Exception:
            return "What you entered for the number of uses allowed isn't even a number."


@validation.PromoCode
def no_unlimited_free_badges(promo_code):
    if promo_code.is_new \
            or promo_code.uses_allowed != promo_code.orig_value_of('uses_allowed') \
            or promo_code.discount != promo_code.orig_value_of('discount') \
            or promo_code.discount_type != promo_code.orig_value_of('discount_type'):
        if promo_code.is_unlimited and promo_code.is_free:
            return 'Unlimited-use, free-badge promo codes are not allowed.'


@validation.PromoCode
def no_dupe_code(promo_code):
    if promo_code.code and (promo_code.is_new or promo_code.code != promo_code.orig_value_of('code')):
        with Session() as session:
            if session.lookup_promo_code(promo_code.code):
                return 'The code you entered already belongs to another ' \
                    'promo code. Note that promo codes are not case sensitive.'


# =============================
# tournaments
# =============================

AttendeeTournament.required = [
    ('first_name', 'First Name'),
    ('last_name', 'Last Name'),
    ('email', 'Email Address'),
    ('game', 'Game Title'),
    ('availability', 'Your Availability'),
    ('format', 'Tournament Format'),
    ('experience', 'Past Experience'),
    ('needs', 'Your Needs'),
    ('why', '"Why?"'),
]


@validation.AttendeeTournament
def attendee_tournament_email(app):
    if not re.match(c.EMAIL_RE, app.email):
        return 'You did not enter a valid email address'


@validation.AttendeeTournament
def attendee_tournament_cellphone(app):
    if app.cellphone and _invalid_phone_number(app.cellphone):
        return 'You did not enter a valid cellphone number'


# =============================
# mivs
# =============================

def _is_invalid_url(url):
    if c.MIVS_SKIP_URL_VALIDATION:
        return False

    try:
        log.debug("_is_invalid_url() is fetching '%s' to check if it's reachable." % url)
        with urlopen(url, timeout=30) as f:
            f.read()
    except Exception:
        return True


IndieStudio.required = [
    ('name', 'Studio Name'),
    ('website', 'Website')
]

IndieDeveloper.required = [
    ('first_name', 'First Name'),
    ('last_name', 'Last Name'),
    ('email', 'Email')
]

IndieGame.required = [
    ('title', 'Game Title'),
    ('brief_description', 'Brief Description'),
    ('genres', 'Genres'),
    ('description', 'Full Description'),
    ('link_to_video', 'Link to Video')
]

IndieGameCode.required = [
    ('code', 'Game Code')
]

IndieJudge.required = [
    ('genres', 'Genres')
]


@validation.IndieStudio
def mivs_new_studio_deadline(studio):
    if studio.is_new and not c.CAN_SUBMIT_MIVS_ROUND_ONE:
        return 'Sorry, but the round one deadline has already passed, so no new studios may be registered'


@validation.IndieStudio
def mivs_valid_url(studio):
    if studio.website and _is_invalid_url(studio.website_href):
        return 'We cannot contact that website; please enter a valid url ' \
            'or leave the website field blank until your website goes online'


@validation.IndieStudio
def mivs_unique_name(studio):
    with Session() as session:
        if session.query(IndieStudio).filter(IndieStudio.name == studio.name, IndieStudio.id != studio.id).count():
            return "That studio name is already taken; " \
                "are you sure you shouldn't be logged in with that studio's account?"


@validation.IndieDeveloper
def mivs_dev_email(dev):
    if not re.match(c.EMAIL_RE, dev.email):
        return 'Please enter a valid email address'


@validation.IndieDeveloper
def mivs_dev_cellphone(dev):
    if (dev.primary_contact or dev.cellphone) and _invalid_phone_number(dev.cellphone):
        return 'Please enter a valid phone number'


@validation.IndieGame
def mivs_platforms_or_other(game):
    if not game.platforms and not game.platforms_text:
        return 'Please select a platform your game runs on or describe another platform in the box provided.'


@validation.IndieGame
def mivs_new_game_deadline(game):
    if game.is_new and not c.CAN_SUBMIT_MIVS_ROUND_ONE:
        return 'Sorry, but the round one deadline has already passed, so no new games may be registered'


@validation.IndieGame
def mivs_instructions(game):
    if game.code_type in c.MIVS_CODES_REQUIRING_INSTRUCTIONS and not game.code_instructions:
        return 'You must leave instructions for how the judges are to use the code(s) you provide'


@validation.IndieGame
def mivs_video_link(game):
    if game.link_to_video and _is_invalid_url(game.video_href):
        return 'The link you provided for the intro/instructional video does not appear to work'


@validation.IndieGame
def mivs_submitted(game):
    if (game.submitted and not game.status == c.ACCEPTED) and not c.HAS_INDIE_ADMIN_ACCESS:
        return 'You cannot edit a game after it has been submitted'


@validation.IndieGame
def mivs_show_info_required_fields(game):
    if game.confirmed:
        if len(game.brief_description) > 80:
            return 'Please make sure your game has a brief description under 80 characters.'
        if not game.link_to_promo_video:
            return 'Please include a link to a 30-second promo video.'
        if game.has_multiplayer and not game.player_count:
            return 'Please tell us how many players your game supports.'
        if game.has_multiplayer and not game.multiplayer_game_length:
            return 'Please enter the average length for a multiplayer game or match.'


@validation.IndieGameImage
def mivs_description(image):
    if image.is_screenshot and not image.description:
        return 'Please enter a description of the screenshot.'


@validation.IndieGameImage
def mivs_valid_type(screenshot):
    if screenshot.extension not in c.MIVS_ALLOWED_SCREENSHOT_TYPES:
        return 'Our server did not recognize your upload as a valid image'


# =============================
# mits
# =============================

MITSTeam.required = [
    ('name', 'Production Team Name')
]

MITSApplicant.required = [
    ('first_name', 'First Name'),
    ('last_name', 'Last Name'),
    ('email', 'Email Address'),
    ('cellphone', 'Cellphone Number')
]

MITSGame.required = [
    ('name', 'Name'),
    ('promo_blurb', 'Promo Blurb'),
    ('description', 'Description'),
    ('genre', 'Game Genre')
]

MITSPicture.required = [
    ('description', 'Description')
]

MITSDocument.required = [
    ('description', 'Description')
]


@validation.MITSTeam
@validation.MITSApplicant
@validation.MITSGame
@validation.MITSPicture
@validation.MITSTimes
def is_saveable(inst):
    team = inst if isinstance(inst, MITSTeam) else inst.team
    if not team.can_save:
        if team.is_new:
            return 'New applications may not be submitted past the deadline'
        else:
            return 'We are now past the deadline and your application may no longer be edited'


@validation.MITSTeam
def address_required_for_sellers(team):
    if team.want_to_sell and not team.address.strip():
        return 'You must provide a business address if you wish to sell your merchandise'


@validation.MITSApplicant
def mits_applicant_email_valid(applicant):
    try:
        validate_email(applicant.email)
    except EmailNotValidError as e:
        return 'Enter a valid email address. ' + str(e)


@validation.MITSApplicant
def valid_phone_number(applicant):
    if _invalid_phone_number(applicant.cellphone):
        return 'Your cellphone number was not a valid 10-digit US phone number. ' \
            'Please include a country code (e.g. +44) for international numbers.'


@validation.MITSGame
def consistent_players(game):
    if game.min_players > game.max_players:
        return 'Min players must be less than or equal to max players'


# =============================
# panels
# =============================

Event.required = [
    ('name', 'Event Name')
]


@validation.Event
def overlapping_events(event, other_event_id=None):
    existing = {}
    for e in event.session.query(Event).filter(Event.location == event.location,
                                               Event.id != event.id,
                                               Event.id != other_event_id).all():
        for hh in e.half_hours:
            existing[hh] = e.name

    for hh in event.half_hours:
        if hh in existing:
            return '"{}" overlaps with the time/duration you specified for "{}"'.format(existing[hh], event.name)


PanelApplication.required = [
    ('name', 'Panel Name'),
    ('description', 'Panel Description'),
    ('length', 'Panel Length')
]

PanelApplicant.required = [
    ('first_name', 'First Name'),
    ('last_name', 'Last Name'),
    ('email', 'Email'),
]


@validation.PanelApplicant
def pa_email(pa):
    if not pa.email or not re.match(c.EMAIL_RE, pa.email):
        return 'Please enter a valid email address'


@validation.PanelApplicant
def pa_phone(pa):
    if (pa.submitter or pa.cellphone) and _invalid_phone_number(pa.cellphone):
        return 'Please enter a valid phone number'


@validation.PanelApplication
def unavailability(app):
    if not app.unavailable and not app.poc_id:
        return 'Your unavailability is required.'


@validation.PanelApplication
def availability(app):
    if not app.available and app.poc_id:
        return 'Please list the times you are available to hold this panel!'


@validation.PanelApplication
def panel_other(app):
    if app.presentation == c.OTHER and not app.other_presentation:
        return 'Since you selected "Other" for your type of panel, please describe it'


@validation.PanelApplication
def app_deadline(app):
    if localized_now() > c.PANEL_APP_DEADLINE and not c.HAS_PANEL_APPS_ACCESS and not app.poc_id:
        return 'We are now past the deadline and are no longer accepting panel applications'


@validation.PanelApplication
def specify_other_time(app):
    if app.length == c.OTHER and not app.length_text:
        return 'Please specify how long your panel will be.'


@validation.PanelApplication
def specify_nonstandard_time(app):
    if app.length != c.SIXTY_MIN and not app.length_reason and not app.poc_id:
        return 'Please explain why your panel needs to be longer than sixty minutes.'


@validation.PanelApplication
def specify_table_needs(app):
    if app.need_tables and not app.tables_desc:
        return 'Please describe how you need tables set up for your panel.'


@validation.PanelApplication
def specify_cost_details(app):
    if app.has_cost and not app.cost_desc:
        return 'Please describe the materials you will provide and how much you will charge attendees for them.'


Attraction.required = [
    ('name', 'Name'),
    ('description', 'Description')
]

AttractionFeature.required = [
    ('name', 'Name'),
    ('description', 'Description')
]


@validation.AttractionEvent
def at_least_one_slot(event):
    if event.slots < 1:
        return 'Events must have at least one slot.'


# =============================
# guests
# =============================

@validation.GuestMerch
def is_merch_checklist_complete(guest_merch):
    if not guest_merch.selling_merch:
        return 'You need to tell us whether and how you want to sell merchandise'

    elif guest_merch.selling_merch == c.ROCK_ISLAND:
        if not guest_merch.poc_is_group_leader and not (
                guest_merch.poc_first_name
                and guest_merch.poc_last_name
                and guest_merch.poc_phone
                and guest_merch.poc_email):
            return 'You must tell us about your merch point of contact'

        elif not (
                guest_merch.poc_zip_code
                and guest_merch.poc_address1
                and guest_merch.poc_city
                and guest_merch.poc_region
                and guest_merch.poc_country):
            return 'You must tell us your complete mailing address'
