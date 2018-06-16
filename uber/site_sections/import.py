import csv
import urllib
from datetime import datetime
from itertools import chain

import cherrypy
import six
from dateutil import parser as dateparser
from pockets import groupify, listify
from pockets.autolog import log
from pytz import UTC
from rpctools.jsonrpc import ServerProxy
from sqlalchemy import or_
from sqlalchemy.types import Date, Boolean, Integer

from uber.config import c
from uber.custom_tags import pluralize
from uber.decorators import all_renderable, ajax_gettable, renderable_override
from uber.errors import HTTPRedirect
from uber.models import Attendee, Choice, Department, DeptMembership, DeptMembershipRequest, DeptRole, Job, \
    MultiChoice, Session, UTCDateTime


def _server_to_url(server):
    if not server:
        return ''
    host = urllib.parse.unquote(server).replace('http://', '').replace('https://', '').split('/')[0]
    return 'https://{}{}'.format(host, c.PATH)


def _server_to_host(server):
    if not server:
        return ''
    return urllib.parse.unquote(server).replace('http://', '').replace('https://', '').split('/')[0]


@all_renderable(c.ADMIN)
class Root:
    def index(self, message='', all_instances=None):
        return {
            'message': message,
            'tables': sorted(model.__name__ for model in Session.all_models()),
            'attendees': all_instances
        }

    def import_model(self, session, model_import, selected_model='', date_format="%Y-%m-%d"):
        model = Session.resolve_model(selected_model)
        message = ''

        cols = {col.name: getattr(model, col.name) for col in model.__table__.columns}
        result = csv.DictReader(model_import.file.read().decode('utf-8').split('\n'))
        id_list = []

        for row in result:
            if 'id' in row:
                id = row.pop('id')  # id needs special treatment

                try:
                    # get the instance if it already exists
                    model_instance = getattr(session, selected_model)(id, allow_invalid=True)
                except Exception:
                    session.rollback()
                    # otherwise, make a new one and add it to the session for when we commit
                    model_instance = model()
                    session.add(model_instance)

            for colname, val in row.items():
                col = cols[colname]
                if not val:
                    # in a lot of cases we'll just have the empty string, so we'll just
                    # do nothing for those cases
                    continue
                if isinstance(col.type, Boolean):
                    if isinstance(val, six.string_types):
                        val = val.strip().lower() not in ('f', 'false', 'n', 'no', '0')
                    else:
                        val = bool(val)
                elif isinstance(col.type, Choice):
                    # the export has labels, and we want to convert those back into their
                    # integer values, so let's look that up (note: we could theoretically
                    # modify the Choice class to do this automatically in the future)
                    label_lookup = {val: key for key, val in col.type.choices.items()}
                    val = label_lookup[val]
                elif isinstance(col.type, MultiChoice):
                    # the export has labels separated by ' / ' and we want to convert that
                    # back into a comma-separate list of integers
                    label_lookup = {val: key for key, val in col.type.choices}
                    vals = [label_lookup[label] for label in val.split(' / ')]
                    val = ','.join(map(str, vals))
                elif isinstance(col.type, UTCDateTime):
                    # we'll need to make sure we use whatever format string we used to
                    # export this date in the first place
                    try:
                        val = UTC.localize(datetime.strptime(val, date_format + ' %H:%M:%S'))
                    except Exception:
                        val = UTC.localize(datetime.strptime(val, date_format))
                elif isinstance(col.type, Date):
                    val = datetime.strptime(val, date_format).date()
                elif isinstance(col.type, Integer):
                    val = int(val)

                # now that we've converted val to whatever it actually needs to be, we
                # can just set it on the model
                setattr(model_instance, colname, val)

            try:
                session.commit()
            except Exception:
                log.error('ImportError', exc_info=True)
                session.rollback()
                message = 'Import unsuccessful'

            id_list.append(model_instance.id)

        all_instances = session.query(model).filter(model.id.in_(id_list)).all() if id_list else None

        return self.index(message, all_instances)

    @renderable_override(c.ACCOUNTS)
    def staff(self, session, target_server='', api_token='', query='', message=''):
        target_url = _server_to_url(target_server)
        if cherrypy.request.method == 'POST':
            try:
                uri = '{}/jsonrpc/'.format(target_url)
                service = ServerProxy(uri=uri, extra_headers={'X-Auth-Token': api_token.strip()})
                results = service.attendee.export(query=query)
            except Exception as ex:
                message = str(ex)
                results = {}
        else:
            results = {}

        attendees = results.get('attendees', [])
        for attendee in attendees:
            attendee['href'] = '{}/registration/form?id={}'.format(target_url, attendee['id'])

        if attendees:
            attendees_by_email = groupify(attendees, lambda a: Attendee.normalize_email(a['email']))
            emails = list(attendees_by_email.keys())
            existing_attendees = session.query(Attendee).filter(Attendee.normalized_email.in_(emails)).all()
            for attendee in existing_attendees:
                attendees_by_email.pop(attendee.normalized_email, {})
            attendees = list(chain(*attendees_by_email.values()))
        else:
            existing_attendees = []

        return {
            'target_server': target_server,
            'api_token': api_token,
            'query': query,
            'message': message,
            'unknown_emails': results.get('unknown_emails', []),
            'unknown_names': results.get('unknown_names', []),
            'attendees': attendees,
            'existing_attendees': existing_attendees,
        }

    @renderable_override(c.ACCOUNTS)
    def shifts(
            self,
            session,
            target_server='',
            api_token='',
            to_department_id='',
            from_department_id='',
            message='',
            **kwargs):

        target_url = _server_to_url(target_server)
        uri = '{}/jsonrpc/'.format(target_url)

        service = None
        if target_url and api_token:
            service = ServerProxy(uri=uri, extra_headers={'X-Auth-Token': api_token.strip()})

        department = {}
        from_departments = []
        if service:
            from_departments = [(id, name) for id, name in sorted(service.dept.list().items(), key=lambda d: d[1])]
            if cherrypy.request.method == 'POST':
                from_host = _server_to_host(uri)
                from_department = service.dept.jobs(department_id=from_department_id)
                to_department = session.query(Department).get(to_department_id)
                from_config = service.config.info()
                FROM_EPOCH = c.EVENT_TIMEZONE.localize(datetime.strptime(from_config['EPOCH'], '%Y-%m-%d %H:%M:%S.%f'))
                EPOCH_DELTA = c.EPOCH - FROM_EPOCH

                to_dept_roles_by_id = to_department.dept_roles_by_id
                to_dept_roles_by_name = to_department.dept_roles_by_name
                dept_role_map = {}
                for from_dept_role in from_department['dept_roles']:
                    to_dept_role = to_dept_roles_by_id.get(from_dept_role['id'], [None])[0]
                    if not to_dept_role:
                        to_dept_role = to_dept_roles_by_name.get(from_dept_role['name'], [None])[0]
                    if not to_dept_role:
                        to_dept_role = DeptRole(
                            name=from_dept_role['name'],
                            description='{}\n\nImported from {}'.format(from_dept_role['description'], from_host),
                            department_id=to_department.id)
                        to_department.dept_roles.append(to_dept_role)
                    dept_role_map[from_dept_role['id']] = to_dept_role

                for from_job in from_department['jobs']:
                    to_job = Job(
                        name=from_job['name'],
                        description='{}\n\nImported from {}'.format(from_job['description'], from_host),
                        duration=from_job['duration'],
                        type=from_job['type'],
                        extra15=from_job['extra15'],
                        slots=from_job['slots'],
                        start_time=UTC.localize(dateparser.parse(from_job['start_time'])) + EPOCH_DELTA,
                        visibility=from_job['visibility'],
                        weight=from_job['weight'],
                        department_id=to_department.id)
                    for from_required_role in from_job['required_roles']:
                        to_job.required_roles.append(dept_role_map[from_required_role['id']])
                    to_department.jobs.append(to_job)

                message = '{} shifts successfully imported from {}'.format(to_department.name, uri)
                raise HTTPRedirect('shifts?target_server={}&api_token={}&message={}', target_server, api_token, message)

        return {
            'target_server': target_server,
            'target_url': uri,
            'api_token': api_token,
            'department': department,
            'to_departments': c.DEPARTMENT_OPTS,
            'from_departments': from_departments,
            'message': message,
        }

    @renderable_override(c.ACCOUNTS)
    @ajax_gettable
    def lookup_departments(self, session, target_server='', api_token='', **kwargs):
        target_url = _server_to_url(target_server)
        uri = '{}/jsonrpc/'.format(target_url)
        try:
            service = ServerProxy(uri=uri, extra_headers={'X-Auth-Token': api_token.strip()})
            return {
                'departments': [(id, name) for id, name in sorted(service.dept.list().items(), key=lambda d: d[1])],
                'target_url': uri,
            }
        except Exception as ex:
            return {
                'error': str(ex),
                'target_url': uri,
            }

    @renderable_override(c.ACCOUNTS)
    def confirm_staff(self, session, target_server, api_token, query, attendee_ids):
        if cherrypy.request.method != 'POST':
            raise HTTPRedirect('staff?target_server={}&api_token={}&query={}', target_server, api_token, query)

        target_url = _server_to_url(target_server)
        results = {}
        try:
            uri = '{}/jsonrpc/'.format(target_url)
            service = ServerProxy(uri=uri, extra_headers={'X-Auth-Token': api_token.strip()})
            results = service.attendee.export(query=','.join(listify(attendee_ids)), full=True)
        except Exception as ex:
            raise HTTPRedirect(
                'staff?target_server={}&api_token={}&query={}&message={}', target_server, api_token, query, str(ex))

        depts = {}

        def _guess_dept(id_name):
            id, name = id_name
            if id in depts:
                return (id, depts[id])

            dept = session.query(Department).filter(or_(
                Department.id == id,
                Department.normalized_name == Department.normalize_name(name))).first()

            if dept:
                depts[id] = dept
                return (id, dept)
            return None

        attendees = results.get('attendees', [])
        for d in attendees:
            import_from_url = '{}/registration/form?id={}'.format(target_url, d['id'])
            old_admin_notes = '\n\nOld Admin Notes:\n{}'.format(d['admin_notes']) if d['admin_notes'] else ''
            assigned_depts = {d[0]: d[1] for d in map(_guess_dept, d.pop('assigned_depts', {}).items()) if d}
            checklist_admin_depts = d.pop('checklist_admin_depts', {})
            dept_head_depts = d.pop('dept_head_depts', {})
            poc_depts = d.pop('poc_depts', {})
            requested_depts = d.pop('requested_depts', {})

            d.update({
                'badge_type': c.STAFF_BADGE,
                'paid': c.NEED_NOT_PAY,
                'placeholder': True,
                'staffing': True,
                'requested_hotel_info': True,
                'admin_notes': 'Imported staff from {}{}'.format(import_from_url, old_admin_notes),
                'ribbon': str(c.DEPT_HEAD_RIBBON) if dept_head_depts else '',
                'past_years': d['all_years'],
            })
            del d['id']
            del d['all_years']

            attendee = Attendee().apply(d, restricted=False)

            for id, dept in assigned_depts.items():
                attendee.dept_memberships.append(DeptMembership(
                    department=dept,
                    attendee=attendee,
                    is_checklist_admin=bool(id in checklist_admin_depts),
                    is_dept_head=bool(id in dept_head_depts),
                    is_poc=bool(id in poc_depts),
                ))

            requested_anywhere = requested_depts.pop('All', False)
            requested_depts = {d[0]: d[1] for d in map(_guess_dept, requested_depts.items()) if d}

            if requested_anywhere:
                attendee.dept_membership_requests.append(DeptMembershipRequest(attendee=attendee))
            for id, dept in requested_depts.items():
                attendee.dept_membership_requests.append(DeptMembershipRequest(
                    department=dept,
                    attendee=attendee,
                ))

            session.add(attendee)

        attendee_count = len(attendees)
        raise HTTPRedirect(
            'staff?target_server={}&api_token={}&query={}&message={}',
            target_server,
            api_token,
            query,
            '{} attendee{} imported'.format(attendee_count, pluralize(attendee_count)))
