from urllib.parse import urlparse

from pockets import listify
from pockets.autolog import log

from uber.config import c


class MenuItem:
    access = None   # list of permission levels allowed to display this menu
    href = None     # link to render
    submenu = None  # submenu to show
    name = None     # name of Menu item to show

    def __init__(self, href=None, access=None, submenu=None, name=None):
        assert submenu or href, "menu items must contain ONE nonempty: href or submenu"
        assert not submenu or not href, "menu items must not contain both a href and submenu"

        if submenu:
            self.submenu = listify(submenu)
        else:
            self.href = href

        self.name = name
        self.access = set(listify(access)) if access else set()

    def append_menu_item(self, m):
        """
        If we're appending a new menu item, and we aren't a submenu, convert us to one now.
        Create a new submenu and append a new item to it with the same name and href as us.

        Example:
            Original menu:
                (name='Rectangles', href="rectangle.html')

            Append to it a new menu item:
                [name='Squares', href='square.html']

            New result is:
                (name='Rectangles', submenu=
                    [
                        (Name='Rectangles', href="rectangle.html"),
                        (Name='Squares', href="square.html")
                    ]
                )
        """
        if not self.submenu and self.href:
            self.submenu = [MenuItem(name=self.name, href=self.href)]
            self.href = None

        self.submenu.append(m)

    def render_items_filtered_by_current_access(self, access_set):
        """
        Returns: dict of menu items which are allowed to be seen by the logged in user's access levels
        """
        out = {}

        if self.access and self.access.isdisjoint(access_set):
            return None

        out['name'] = self.name
        if self.submenu:
            out['submenu'] = []
            for menu_item in self.submenu:
                filtered_menu_items = menu_item.render_items_filtered_by_current_access(access_set)
                if filtered_menu_items:
                    out['submenu'].append(filtered_menu_items)
        else:
            out['href'] = self.href

        return out

    def __getitem__(self, key):
        for sm in self.submenu:
            if sm.name == key:
                return sm


def get_external_schedule_menu_name():
    if getattr(c, 'ALT_SCHEDULE_URL', ''):
        try:
            url = urlparse(c.ALT_SCHEDULE_URL)
            return 'View External Public Schedule on {}'.format(url.netloc)
        except Exception:
            log.warning('Menu: Unable to parse ALT_SCHEDULE_URL: "{}"', c.ALT_SCHEDULE_URL)
            return 'View External Public Schedule'

    return 'View Public Schedule'


c.MENU = MenuItem(name='Root', submenu=[
    MenuItem(name='Admin', submenu=[
        MenuItem(name='Admin Accounts', href='../accounts/', access=c.ACCOUNTS),
        MenuItem(name='API Access', href='../api/', access=list(c.API_ACCESS.keys())),
        MenuItem(name='Pending Emails', href='../emails/pending', access=c.PEOPLE),
        MenuItem(name='Jobs', href='../jobs/', access=c.PEOPLE),
        MenuItem(name='All Unfilled Shifts', href='../jobs/everywhere', access=c.PEOPLE),
        MenuItem(name='Departments', href='../departments/', access=c.PEOPLE),
        MenuItem(name='Department Checklists', href='../dept_checklist/overview', access=c.PEOPLE),
        MenuItem(name='Feed of Database Changes', href='../registration/feed', access=[c.ACCOUNTS, c.STAFF_ROOMS]),
    ]),

    MenuItem(name='People', access=[c.PEOPLE, c.REG_AT_CON], submenu=[
        MenuItem(name='Attendees', href='../registration/{}'.format('?invalid=True' if c.AT_THE_CON else '')),
        MenuItem(name='Groups', href='../groups/'),
        MenuItem(name='Bands', href='../guest_admin/?filter=only-bands', access=c.BANDS),
        MenuItem(name='Guests', href='../guest_admin/?filter=only-guests', access=c.BANDS),
        MenuItem(name='Watchlist', href='../registration/watchlist_entries', access=c.WATCHLIST),
    ]),

    MenuItem(name='Schedule', access=[c.STUFF, c.PEOPLE, c.REG_AT_CON], submenu=[
        MenuItem(name=get_external_schedule_menu_name(), href='../schedule/'),
        MenuItem(name='Edit Schedule', access=c.STUFF, href='../schedule/edit'),
    ]),

    MenuItem(name='Statistics', access=c.STATS, submenu=[
        MenuItem(name='Summary', href='../summary/'),
        MenuItem(name='Graphs', href='../graphs/'),
    ]),
])


if c.ATTRACTIONS_ENABLED:
    c.MENU['Schedule'].append_menu_item(MenuItem(name='Attractions', href='../attractions_admin/'))
