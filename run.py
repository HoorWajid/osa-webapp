# run.py
# Security fixes applied:
#   1. debug=True removed — reads from FLASK_DEBUG env var (default False)
#   2. host restricted to 127.0.0.1 in development — reads from env var
#   3. Port reads from PORT env var (default 5000)
#   4. Production guard: refuses to start with debug=True if ENV=production
#   5. Clear startup message distinguishing dev vs production mode

import os
import sys
from dotenv import load_dotenv

load_dotenv()

from app import create_app, db
from app.models import User, UserProfile, PatientAssignment, DataFile, Analysis

app = create_app()


@app.shell_context_processor
def make_shell_context():
    return {
        'db': db,
        'User': User,
        'UserProfile': UserProfile,
        'PatientAssignment': PatientAssignment,
        'DataFile': DataFile,
        'Analysis': Analysis,
    }


@app.cli.command()
def init_db():
    """Initialize the database — creates all tables."""
    db.create_all()
    print('Database initialized.')


if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    env   = os.environ.get('FLASK_ENV', 'development').lower()
    host  = os.environ.get('FLASK_HOST', '127.0.0.1')   # FIX: localhost only by default
    port  = int(os.environ.get('PORT', 5000))

    # SECURITY FIX: Hard block — never allow debug mode in production
    # Like a surgeon's checklist: if this condition is wrong, stop everything.
    if env == 'production' and debug:
        print('\n[SECURITY] FLASK_DEBUG=true is not allowed when FLASK_ENV=production.')
        print('           Set FLASK_DEBUG=false or remove it from your .env file.\n')
        sys.exit(1)

    if debug:
        print(f'\n[DEV]  Flask dev server on http://{host}:{port}')
        print(       '       Debug mode ON — never use this in production\n')
    else:
        print(f'\n[INFO] Starting on http://{host}:{port}')
        print(       '[INFO] For production, use Gunicorn instead:')
        print(       '       gunicorn "app:create_app()" --bind 0.0.0.0:8000 --workers 4\n')

    app.run(host=host, port=port, debug=debug)