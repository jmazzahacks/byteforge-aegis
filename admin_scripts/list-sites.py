#!/usr/bin/env python
"""
Script to list all sites in the auth service.
"""
import os
import sys
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def get_input(prompt: str, default: str = None) -> str:
    """Get user input with optional default value"""
    if default:
        user_input = input(f"{prompt} [{default}]: ").strip()
        return user_input if user_input else default
    else:
        while True:
            user_input = input(f"{prompt}: ").strip()
            if user_input:
                return user_input
            print("This field is required. Please try again.")


def main():
    print("=" * 60)
    print("List Sites - Multi-Tenant Auth Service")
    print("=" * 60)
    print()

    # Get API configuration
    api_url = os.getenv('API_URL', 'http://127.0.0.1:5678')
    api_key = os.getenv('MASTER_API_KEY')

    if not api_key:
        api_key = get_input("Master API Key (or set MASTER_API_KEY in .env)")

    # Make API request
    print("Fetching sites...")
    try:
        response = requests.get(
            f"{api_url}/api/sites",
            headers={
                'X-API-Key': api_key,
                'Content-Type': 'application/json'
            }
        )

        if response.status_code == 200:
            sites = response.json()

            if not sites:
                print("\nNo sites found.")
                return

            print(f"\nFound {len(sites)} site(s):")
            print("=" * 60)

            for site in sites:
                print(f"\nID: {site['id']}")
                print(f"  Name:         {site['name']}")
                print(f"  Domain:       {site['domain']}")
                print(f"  Frontend URL: {site['frontend_url']}")
                redirect_url = site.get('verification_redirect_url') or '(not set)'
                print(f"  Verify Redirect: {redirect_url}")
                self_reg = "Enabled" if site.get('allow_self_registration', True) else "Disabled"
                print(f"  Self-Registration: {self_reg}")
                print(f"  Email From:   {site['email_from_name']} <{site['email_from']}>")
                print(f"  Created:      {site['created_at']}")

            print()
            print("=" * 60)
        else:
            print(f"\n✗ Error listing sites (HTTP {response.status_code}):")
            print(response.json())
            sys.exit(1)

    except requests.exceptions.ConnectionError:
        print(f"\n✗ Error: Could not connect to {api_url}")
        print("Is the auth service running?")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nCancelled.")
        sys.exit(0)
