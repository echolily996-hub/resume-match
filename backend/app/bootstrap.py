import os
import subprocess
import sys

from .config import Settings
from .db import make_engine
from .seed import seed_users


def main():
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], check=True)
    engine = make_engine(Settings.from_env().database_url)
    try:
        seed_users(engine)
    finally:
        engine.dispose()
    os.execv(sys.executable, [sys.executable, '-m', 'uvicorn', 'app.main:app',
                            '--host', '0.0.0.0', '--port', '8000', '--workers', '1'])


if __name__ == '__main__':
    main()
