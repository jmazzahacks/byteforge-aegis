#!/usr/bin/env python
"""
Script to delete expired tokens from the auth service.

Safe to run at any time and safe to run twice: only rows whose expiry has
already passed are removed, and an expired token is rejected at validation
anyway. Intended to be run on a schedule.

Exits non-zero on failure so a cron wrapper notices.
"""
import os
import sys
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Without this a hung service would block a cron-driven run forever and let
# successive runs pile up, which is the opposite of the non-zero exit this
# script promises its caller. Generous, since the first run after a long gap
# has the most rows to clear.
REQUEST_TIMEOUT_SECONDS = 60

TABLES = [
    ('auth_tokens', 'Auth tokens'),
    ('refresh_tokens', 'Refresh tokens'),
    ('email_verification_tokens', 'Email verification'),
    ('password_reset_tokens', 'Password resets'),
    ('email_change_requests', 'Email changes'),
]


def main():
    print("=" * 60)
    print("Cleanup Expired Tokens - Multi-Tenant Auth Service")
    print("=" * 60)
    print()

    api_url = os.getenv('API_URL', 'http://127.0.0.1:5678')
    api_key = os.getenv('MASTER_API_KEY')

    if not api_key:
        print("✗ Error: MASTER_API_KEY is not set.")
        print("Set it in .env or the environment; this script is meant to run")
        print("unattended, so it does not prompt.")
        sys.exit(1)

    print("Removing expired tokens...")
    try:
        response = requests.post(
            f"{api_url}/api/admin/cleanup-expired-tokens",
            headers={
                'X-API-Key': api_key,
                'Content-Type': 'application/json'
            },
            timeout=REQUEST_TIMEOUT_SECONDS
        )

        if response.status_code == 200:
            result = response.json()

            print()
            for key, label in TABLES:
                print(f"  {label:22} {result.get(key, 0)}")
            print("=" * 60)
            print(f"  {'Total removed':22} {result.get('total', 0)}")
            print()
        else:
            print(f"\n✗ Error cleaning up tokens (HTTP {response.status_code}):")
            print(response.text)
            sys.exit(1)

    except requests.exceptions.ConnectionError:
        print(f"\n✗ Error: Could not connect to {api_url}")
        print("Is the auth service running?")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
