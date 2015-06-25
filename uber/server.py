from uber.common import *

mimetypes.init()


def _add_email():
    [body] = cherrypy.response.body
    body = body.replace(b'<body>', b'''<body>Please contact us via <a href="CONTACT_URL">CONTACT_URL</a> if you're not sure why you're seeing this page.'''.replace(b'CONTACT_URL', c.CONTACT_URL.encode('utf-8')))
    cherrypy.response.headers['Content-Length'] = len(body)
    cherrypy.response.body = [body]
cherrypy.tools.add_email_to_error_page = cherrypy.Tool('after_error_response', _add_email)


class StaticViews:
    def path_args_to_string(self, path_args):
        return '/'.join(path_args)

    def get_full_path_from_path_args(self, path_args):
        return 'static_views/' + self.path_args_to_string(path_args)

    def get_filename_from_path_args(self, path_args):
        return path_args[-1]

    @cherrypy.expose
    def magfest_js(self):
        """
        We have several Angular apps which need to be able to access our constants like c.ATTENDEE_BADGE and such.
        We also need those apps to be able to make HTTP requests with CSRF tokens, so we set that default.
        """
        cherrypy.response.headers['Content-Type'] = 'text/javascript'
        renderable = {k: v for k, v in renderable_data().items() if isinstance(v, (bool, int, str))}
        consts = json.dumps(renderable, indent=4)
        return '\n'.join([
            'angular.module("magfest", [])',
            '.constant("magconsts", {})'.format(consts),
            '.run(function ($http) {',
            '   $http.defaults.headers.common["CSRF-Token"] = "{}";'.format(renderable.get('CSRF_TOKEN')),
            '});'
        ])

    @cherrypy.expose
    def default(self, *path_args, **kwargs):
        content_filename = self.get_filename_from_path_args(path_args)

        template_name = self.get_full_path_from_path_args(path_args)
        content = render(template_name)

        guessed_content_type = mimetypes.guess_type(content_filename)[0]
        return cherrypy.lib.static.serve_fileobj(content, name=content_filename, content_type=guessed_content_type)


@all_renderable()
class Root:
    def index(self):
        raise HTTPRedirect('common/')

    def common_js(self):
        cherrypy.response.headers['Content-Type'] = 'text/javascript'
        return render('common.js')

    static_views = StaticViews()

mount_site_sections(c.MODULE_ROOT)

cherrypy.tree.mount(Root(), c.PATH, c.APPCONF)
static_overrides(join(c.MODULE_ROOT, 'static'))

DaemonTask(check_unassigned, interval=300)
DaemonTask(detect_duplicates, interval=300)
DaemonTask(check_placeholders, interval=300)
DaemonTask(AutomatedEmail.send_all, interval=300)

# TODO: this should be replaced by something a little cleaner, but it can be a useful debugging tool
# DaemonTask(lambda: log.error(Session.engine.pool.status()), interval=5)
