"""Create an independent admin after migrations; passwords are never printed to logs."""

import argparse
import json
import os
import secrets
from pathlib import Path

from app.core.config import Settings
from app.core.security import Security
from app.db.repository import Database
from app.modules.admin.auth import create_admin
from app.modules.admin.security import PERMISSIONS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login-name", default="admin")
    parser.add_argument("--display-name", default="超级管理员")
    parser.add_argument("--role", choices=PERMISSIONS, default="SUPER_ADMIN")
    parser.add_argument("--credentials-file", default="var/admin-bootstrap.json")
    parser.add_argument("--enable-mfa", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    password = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD") or secrets.token_urlsafe(24)
    mfa = None
    if args.enable_mfa or settings.environment == "production":
        import base64

        mfa = base64.b32encode(os.urandom(20)).decode()
    path = Path(args.credentials_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Refuse overwriting existing credentials; create the secure file before the DB transaction.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    db = Database(settings)
    try:
        with os.fdopen(fd, "w") as output:
            with db.session() as session:
                admin = create_admin(
                    session, args.login_name, args.display_name, password, args.role, mfa, Security(settings)
                )
                output.write(
                    json.dumps(
                        {
                            "login_name": args.login_name,
                            "password": password,
                            "mfa_secret": mfa,
                            "admin_id": admin.id,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
        print("管理员已创建；凭证仅保存在权限为 0600 的本地文件：" + str(path))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        db.engine.dispose()


if __name__ == "__main__":
    main()
