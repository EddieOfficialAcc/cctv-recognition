"""Create a login account without putting a password in shell history."""
import argparse
import getpass
import logging

import pyodbc
from pydantic import ValidationError

from app.auth import create_user


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a CCTV application account.")
    parser.add_argument("username")
    args = parser.parse_args()
    password = getpass.getpass("Password (at least 12 characters): ")
    if password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords do not match.")
    try:
        create_user(args.username, password)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    except (pyodbc.Error, ValidationError) as error:
        logging.error("Account creation failed (%s).", type(error).__name__)
        raise SystemExit("Check database configuration, schema, permissions, and whether the username already exists.") from None
    print("Account created.")


if __name__ == "__main__":
    main()
