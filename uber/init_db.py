from uber.common import *

verbose = dry_run = False

class Style:
    def __getattr__(self, name):
        return lambda text: text

with open(join(MODULE_ROOT, 'models.py')) as f:
    text = f.read()

classes = sorted(all_models(), key=lambda c: text.index('class {}('.format(c.__name__)))

def does_attendee_table_already_exist(cursor):
    cursor.execute("select exists(select * from information_schema.tables where table_name='Attendee')", ('mytable',))
    return cursor.fetchone()[0]

def drop_and_create(cursor):
    for model in reversed(classes):
        sql = 'DROP TABLE IF EXISTS "{}";'.format(model.__name__)
        if verbose:
            print(sql)
        if not dry_run:
            cursor.execute(sql)

    for model in classes:
        sql = connection.creation.sql_create_model(model, Style(), classes)[0][0]
        if verbose:
            print(sql)
        if not dry_run:
            cursor.execute(sql)

def insert_admin():
    attendee = Attendee.objects.create(
        placeholder = True,
        first_name  = 'Test',
        last_name   = 'Developer',
        email       = 'magfest@example.com',
        badge_type  = STAFF_BADGE,
        ribbon      = DEPT_HEAD_RIBBON
    )
    AdminAccount.objects.create(
        attendee = attendee,
        access   = ','.join(str(level) for level, name in ACCESS_OPTS),
        hashed   = bcrypt.hashpw('magfest', bcrypt.gensalt())
    )

def init_db():
    with closing(connection.cursor()) as cursor:
        if (does_attendee_table_already_exist(cursor)):
            if not force_init:
                print("ERROR: data already exists in this database, not going to init.\n"
                      "Use --force-init to override. WARNING: YOU WILL LOSE ALL DATA")
                return
            else:
                print("WARNING: --force-init specified, deleting everything the database.")

        drop_and_create(cursor)

    if not dry_run:
        insert_admin()

if __name__ == '__main__':
    verbose = '--quiet' not in sys.argv
    dry_run = '--dry-run' in sys.argv
    force_init = '--force-init' in sys.argv

    if any(not arg.startswith('-') for arg in sys.argv[1:]):
        classes = [c for c in classes if c.__name__ in sys.argv[1:]]

    init_db()