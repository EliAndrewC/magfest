from uber.common import *
from uber.models.email import EmailDaemonStatus, EmailDaemonCategoryResult
import uber.scheduler


class AutomatedEmail:
    """
    Represents one category of emails that we send out.
    An example of an email category would be "Your registration has been confirmed".
    """

    # global: all instances of every registered email category in the system
    instances = OrderedDict()

    # a list of queries to run during each automated email sending run to
    # return particular model instances of a given type.
    queries = {
        Attendee: lambda session: session.all_attendees().options(
            subqueryload(Attendee.admin_account),
            subqueryload(Attendee.group),
            subqueryload(Attendee.shifts)
                .subqueryload(Shift.job),
            subqueryload(Attendee.assigned_depts),
            subqueryload(Attendee.dept_membership_requests),
            subqueryload(Attendee.checklist_admin_depts)
                .subqueryload(Department.dept_checklist_items),
            subqueryload(Attendee.dept_memberships),
            subqueryload(Attendee.dept_memberships_with_role),
            subqueryload(Attendee.depts_where_working)),
        Group: lambda session: session.query(Group).options(
            subqueryload(Group.attendees)).order_by(Group.id)
    }

    def __init__(self, model, subject, template, filter, ident, *, when=(),
                 sender=None, extra_data=None, cc=None, bcc=None,
                 post_con=False, needs_approval=True, allow_during_con=False):

        self.subject = subject.format(EVENT_NAME=c.EVENT_NAME, EVENT_DATE=c.EPOCH.strftime("(%b %Y)"))
        self.ident = ident

        assert self.ident, 'error: automated email ident may not be empty.'
        assert self.ident not in self.instances, 'error: automated email ident "{}" is registered twice.'.format(self.ident)

        self.instances[self.ident] = self

        self.model, self.template, self.needs_approval, self.allow_during_con = model, template, needs_approval, allow_during_con
        self.cc = cc or []
        self.bcc = bcc or []
        self.extra_data = extra_data or {}
        self.sender = sender or c.REGDESK_EMAIL
        self.when = listify(when)

        assert filter is not None

        if post_con:
            self.filter = lambda model_inst: c.POST_CON and filter(model_inst)
        else:
            self.filter = lambda model_inst: not c.POST_CON and filter(model_inst)

    def filters_run(self, model_inst):
        return all([self.filter(model_inst), self._run_date_filters()])

    def _run_date_filters(self):
        return all([date_filter() for date_filter in self.when])

    def __repr__(self):
        return '<{}: {!r}>'.format(self.__class__.__name__, self.subject)

    def computed_subject(self, x):
        """
        Given a model instance, return an email subject email for that instance.
        By default this just returns the default subject unmodified; this method
        exists only to be overridden in subclasses.  For example, we might want
        our panel email subjects to contain the name of the panel.
        """
        return self.subject

    def _already_sent(self, model_inst):
        """
        Returns true if we have a record of previously sending this email to this model

        NOTE: c.PREVIOUSLY_SENT_EMAILS is a cached property and will only update at the start of each daemon run.
        """
        return (model_inst.__class__.__name__, model_inst.id, self.ident) in c.PREVIOUSLY_SENT_EMAILS

    def send_if_should(self, model_inst, raise_errors=False):
        """
        If it's OK to send an email of our category to this model instance (i.e. a particular Attendee) then send it.

        Do any error handling in the client functions we call
        """
        if self._should_send(model_inst, raise_errors=raise_errors):
            self.really_send(model_inst, raise_errors=raise_errors)

    def _should_send(self, model_inst, raise_errors=False):
        """
        If True, we should generate an actual email created from our email category
        and send it to a particular model instance.

        This is determined based on a few things like:
        1) whether we have sent this exact email out yet or not
        2) whether the email category has been approved
        3) whether the model instance passed in is the same type as what we want to process
        4) do any date-based filters exist on this email category? (i.e. send 7 days before magfest)
        5) do any other filters exist on this email category? (i.e. only if attendee.staffing == true)

        Example #1 of a model instance to check:
          self.ident: "You {attendee.name} have registered for our event!"
          model_inst:  class Attendee: id #4532, name: "John smith"

        Example #2 of a model instance to check:
          self.ident: "Your group {group.name} owes money"
          model_inst:  class Group: id #1251, name: "The Fighting Mongooses"

        :param model_inst: The model we've been requested to use (i.e. Attendee, Group, etc)

        :return: True if we should send this email to this model instance, False if not.
        """

        try:
            return all(condition() for condition in [
                lambda: not c.AT_THE_CON or self.allow_during_con,
                lambda: isinstance(model_inst, self.model),
                lambda: getattr(model_inst, 'email', None),
                lambda: not self._already_sent(model_inst),
                lambda: self.filters_run(model_inst),
                lambda: self.approved,
            ])
        except:
            log.error('error determining whether to send {!r} email to {}', self.subject, model_inst.email, exc_info=True)
            if raise_errors:
                raise
            return False

    @property
    def approved(self):
        """
        Check if this email category has been approved by the admins to send automated emails.

        :return: True if we are approved to send this email, or don't need approval. False otherwise

        Side effect: If running as part of the automated email daemon code, and we aren't approved, log the count of
        emails that would have been sent so we can report it via the UI later.
        """

        approved_to_send = not self.needs_approval or self.ident in c.EMAIL_APPROVED_IDENTS

        if not approved_to_send:
            # log statistics about how many emails would have been sent if we had approval.
            # if running as part of a daemon, this will record the data.
            SendAllAutomatedEmailsJob.log_unsent_because_unapproved(self)

        return approved_to_send

    def render(self, model_instance):
        model = getattr(model_instance, 'email_model_name', model_instance.__class__.__name__.lower())
        return render('emails/' + self.template, dict({model: model_instance}, **self.extra_data))

    def really_send(self, model_instance, raise_errors=False):
        """
        Actually send an email to a particular model instance (i.e. a particular attendee).

        Doesn't perform any kind of checks at all if we should be sending this, just immediately sends the email
        no matter what.

        NOTE: use send_if_should() instead of calling this method unless you 100% know what you're doing.
        NOTE: send_email() fails if c.SEND_EMAILS is False
        """
        try:
            subject = self.computed_subject(model_instance)
            format = 'text' if self.template.endswith('.txt') else 'html'
            send_email(self.sender, model_instance.email, subject,
                       self.render(model_instance), format,
                       model=model_instance, cc=self.cc, ident=self.ident)
        except:
            log.error('error sending {!r} email to {}', self.subject, model_instance.email, exc_info=True)
            if raise_errors:
                raise

    @property
    def when_txt(self):
        """
        Return a textual description of when the date filters are active for this email category.
        """

        return '\n'.join([filter.active_when for filter in self.when])


class SendAllAutomatedEmailsJob:

    run_lock = threading.Lock()

    @classmethod
    def send_all_emails(cls, raise_errors=False):
        """ Helper method to start a run of our automated email processing """
        cls().run(raise_errors)

    def run(self, raise_errors=False):
        """
        Do one run of our automated email service.  Call this periodically to send any emails that should go out
        automatically.

        This will NOT run if we're on-site, or not configured to send emails.

        :param raise_errors: If False, exceptions are squashed during email sending and we'll try the next email.
        """
        if not (c.DEV_BOX or c.SEND_EMAILS):
            return

        if not SendAllAutomatedEmailsJob.run_lock.acquire(blocking=False):
            log.warn("can't acquire lock for email daemon (already running?), skipping this run.")
            return

        try:
            self._run(raise_errors)
        finally:
            SendAllAutomatedEmailsJob.run_lock.release()

    def _run(self, raise_errors):
        with Session() as session:
            # performance: we use request_cached_context() to force cache invalidation
            # of variables like c.EMAIL_APPROVED_IDENTS
            with request_cached_context(clear_cache_on_start=True):
                self._init(session, raise_errors)
                self._send_all_emails()
                self._on_finished_run()

    def _init(self, session, raise_errors):
        self.session = session
        self.raise_errors = raise_errors
        self.results = defaultdict(EmailDaemonCategoryResult)

        # note: this will get cleared after request_cached_context object is released.
        assert not threadlocal.get('currently_running_email_daemon')
        threadlocal.set('currently_running_email_daemon', self)

    def _on_finished_run(self):
        # save the results of the last run for future use
        self.session.query(EmailDaemonStatus).delete()
        self.session.query(EmailDaemonCategoryResult).delete()

        self.session.add(EmailDaemonStatus())  # updates the completion time
        self.session.add_all(self.results.values())

        self.session.commit()

    def _send_all_emails(self):
        """
        This function is the heart of the automated email daemon in ubersystem
        and is called once every couple of minutes.

        To send automated emails, we look at AutomatedEmail.queries for a list of DB queries to run.
        The result of these queries are a list of model instances that we might want to send emails for.

        These model instances will be of type 'MagModel'. Examples: 'Attendee', 'Group'.
        Each model instance is, for example, a particular group, or a particular attendee.

        Next, we'll go through *ALL* AutomatedEmail's that are registered in the system.
        (When you see AutomatedEmail think "email category").  On each of these we'll ask that
        email category if it wants to send any emails for this particular model (i.e. a specific attendee).

        If that automated email decides the time is right (i.e. it hasn't sent the email already, the attendee has a
        valid email address, email has been approved for sending, and a bunch of other stuff), then it will actually
        send an email for this model instance.
        """
        for model, query_fn in AutomatedEmail.queries.items():
            model_instances = query_fn(self.session)
            for model_instance in model_instances:
                sleep(0.01)  # throttle CPU usage
                self._send_any_emails_for(model_instance)

    def _send_any_emails_for(self, model_instance):
        """
        Go through every email category in the system and ask it if it wants to send any email on behalf of this
        particular model instance.

        An example of a model + category combo to check:
          email_category: "You {attendee.name} have registered for our event!"
          model_instance:  Attendee #42
        """
        for email_category in AutomatedEmail.instances.values():
            email_category.send_if_should(model_instance, self.raise_errors)

    @classmethod
    def _currently_running_daemon_on_this_thread(cls):
        return threadlocal.get('currently_running_email_daemon')

    @classmethod
    def log_unsent_because_unapproved(cls, automated_email_category):
        running_daemon = cls._currently_running_daemon_on_this_thread()
        if running_daemon:
            running_daemon._increment_unsent_because_unapproved_count(automated_email_category)

    def _increment_unsent_because_unapproved_count(self, automated_email_category):
        """
        Log information that a particular email wanted to send out an email, but could not because it didn't have
        approval.

        :param automated_email_category: The category that wanted to send but needed approval
        """

        category = self.results[automated_email_category.ident]
        category.unsent_because_unapproved += 1
        category.ident = automated_email_category.ident


class StopsEmail(AutomatedEmail):
    def __init__(self, subject, template, filter, ident, **kwargs):
        AutomatedEmail.__init__(self, Attendee, subject, template, lambda a: a.staffing and filter(a), ident, sender=c.STAFF_EMAIL, **kwargs)


class GuestEmail(AutomatedEmail):
    def __init__(self, subject, template, ident, filter=lambda a: True, **kwargs):
        AutomatedEmail.__init__(self, Attendee, subject, template, lambda a: a.badge_type == c.GUEST_BADGE and filter(a), ident=ident, sender=c.GUEST_EMAIL, **kwargs)


class GroupEmail(AutomatedEmail):
    def __init__(self, subject, template, filter, ident, **kwargs):
        AutomatedEmail.__init__(self, Group, subject, template, lambda g: not g.is_dealer and filter(g), ident, sender=c.REGDESK_EMAIL, **kwargs)


class MarketplaceEmail(AutomatedEmail):
    def __init__(self, subject, template, filter, ident, **kwargs):
        AutomatedEmail.__init__(self, Group, subject, template, lambda g: g.is_dealer and filter(g), ident, sender=c.MARKETPLACE_EMAIL, **kwargs)


class DeptChecklistEmail(AutomatedEmail):
    def __init__(self, conf):
        AutomatedEmail.__init__(self, Attendee,
                                subject='{EVENT_NAME} Department Checklist: ' + conf.name,
                                template='shifts/dept_checklist.txt',
                                filter=lambda a: a.admin_account and any(
                                    not d.checklist_item_for_slug(conf.slug)
                                    for d in a.checklist_admin_depts),
                                ident='department_checklist_{}'.format(conf.name),
                                when=days_before(10, conf.deadline),
                                sender=c.STAFF_EMAIL,
                                extra_data={'conf': conf},
                                post_con=conf.email_post_con or False)


def notify_admins_of_any_pending_emails():
    """
    Generate an email a report which alerts admins that there are emails which are ready to send,
    but won't because they need approval from an admin.

    This is useful so we don't forget to let certain categories of emails send.
    """
    if not c.ENABLE_PENDING_EMAILS_REPORT or not c.PRE_CON or not (c.DEV_BOX or c.SEND_EMAILS):
        return

    pending_email_categories = get_pending_email_data()
    if not pending_email_categories:
        return

    for sender, email_categories in pending_email_categories.items():
        include_all_categories = sender == c.STAFF_EMAIL
        included_categories = pending_email_categories

        if not include_all_categories:
            included_categories = {
                c_sender: categories for c_sender, categories in pending_email_categories.items() if c_sender == sender
            }

        send_pending_email_report(included_categories, sender)


def send_pending_email_report(pending_email_categories, sender):
    rendering_data = {
        'pending_email_categories': pending_email_categories,
        'primary_sender': sender,
    }
    subject = c.EVENT_NAME + ' Pending Emails Report for ' + localized_now().strftime('%Y-%m-%d')
    body = render('emails/daily_checks/pending_emails.html', rendering_data)
    send_email(c.STAFF_EMAIL, sender, subject, body, format='html', model='n/a')


uber.scheduler.register_task(
    fn=lambda: uber.scheduler.schedule.every().day.at("06:00").do(notify_admins_of_any_pending_emails),
    category="reports"
)


def get_pending_email_data():
    """
    Generate a list of emails which are ready to send, but need approval.

    Returns: A dict of senders -> email idents -> pending counts for any email category with pending emails,
    or None if none are waiting to send or the email daemon service has not finished any runs yet.
    """

    with Session() as session:
        if not EmailDaemonStatus.last_result_looks_valid(session):
            return None

        categories_results = session.query(EmailDaemonCategoryResult).all()
        if len(categories_results) <= 0:
            return None

        pending_emails_by_sender = defaultdict(dict)

        for automated_email in AutomatedEmail.instances.values():
            sender = automated_email.sender
            ident = automated_email.ident

            category_results = next((result for result in categories_results if result.ident == ident), None)
            if not category_results:
                continue

            if category_results.unsent_because_unapproved <= 0:
                continue

            pending_emails_by_sender[sender][ident] = {
                'num_unsent': category_results.unsent_because_unapproved,
                'subject': automated_email.subject,
                'sender': automated_email.sender,
            }

        return pending_emails_by_sender


uber.scheduler.register_task(
    lambda: uber.scheduler.schedule.every(30).seconds.do(SendAllAutomatedEmailsJob.send_all_emails),
    category="automated_email_sending",
)


@entry_point
def start_email_sending_daemon():
    initialize_db()
    uber.scheduler.start_scheduler_and_block(include_categories="automated_email_sending")