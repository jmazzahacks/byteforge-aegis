#!/usr/bin/env python
"""
Bootstrap script for Aegis administration.
Creates the admin site and admin user in one step.
"""
import os
import sys
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def get_input(prompt: str, default: str = None, required: bool = True) -> str:
    """Get user input with optional default value"""
    if default:
        user_input = input(f"{prompt} [{default}]: ").strip()
        return user_input if user_input else default
    else:
        while True:
            user_input = input(f"{prompt}: ").strip()
            if user_input:
                return user_input
            if not required:
                return ""
            print("This field is required. Please try again.")


def create_site(api_url: str, api_key: str, site_data: dict) -> dict:
    """Create the admin site via the API"""
    response = requests.post(
        f"{api_url}/api/sites",
        headers={
            'X-API-Key': api_key,
            'Content-Type': 'application/json'
        },
        json=site_data
    )

    if response.status_code == 201:
        return response.json()
    else:
        print(f"\n✗ Error creating admin site (HTTP {response.status_code}):")
        print(response.json())
        sys.exit(1)


def create_admin_user(api_url: str, api_key: str, site_id: int, email: str) -> dict:
    """Create the admin user via the API"""
    response = requests.post(
        f"{api_url}/api/admin/register",
        headers={
            'X-API-Key': api_key,
            'Content-Type': 'application/json'
        },
        json={
            'site_id': site_id,
            'email': email,
            'role': 'admin'
        }
    )

    if response.status_code == 201:
        return response.json()
    else:
        print(f"\n✗ Error creating admin user (HTTP {response.status_code}):")
        print(response.json())
        sys.exit(1)


def main():
    print("=" * 60)
    print("Bootstrap Aegis Administration")
    print("=" * 60)
    print()
    print("This script creates the Aegis admin site and admin user.")
    print("Run this once after installing Aegis.")
    print()

    # Step 1: API configuration
    print("Step 1: API Configuration")
    print("-" * 60)

    default_api_url = os.getenv('API_URL', 'http://127.0.0.1:5678')
    api_url = get_input("Auth service URL", default_api_url)

    api_key = os.getenv('MASTER_API_KEY')
    if not api_key:
        api_key = get_input("Master API Key (or set MASTER_API_KEY in .env)")
    else:
        print(f"Master API Key: (loaded from environment)")

    # Step 2: Admin site details
    print()
    print("Step 2: Admin Site Details")
    print("-" * 60)

    default_domain = os.getenv('AEGIS_ADMIN_DOMAIN', '')
    admin_domain = get_input("Admin site domain (e.g., 'aegis.yourdomain.com')", default_domain or None)

    admin_site_name = get_input("Admin site name", "Aegis Administration")

    default_frontend_url = os.getenv('AEGIS_FRONTEND_URL', '')
    frontend_url = get_input("Frontend URL (e.g., 'https://aegis.yourdomain.com')", default_frontend_url or None)

    email_from = get_input("Email from address (e.g., 'noreply@yourdomain.com')")
    email_from_name = get_input("Email from name", "Aegis Administration")

    # Step 3: Admin user
    print()
    print("Step 3: Admin User")
    print("-" * 60)

    admin_email = get_input("Admin user email address")

    # Summary and confirmation
    print()
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  API URL:          {api_url}")
    print(f"  Admin Domain:     {admin_domain}")
    print(f"  Site Name:        {admin_site_name}")
    print(f"  Frontend URL:     {frontend_url}")
    print(f"  Email From:       {email_from_name} <{email_from}>")
    print(f"  Admin Email:      {admin_email}")
    print(f"  Self-Registration: Disabled")
    print("=" * 60)

    confirm = input("\nProceed with bootstrap? (y/n): ").strip().lower()
    if confirm not in ('y', 'yes'):
        print("Cancelled.")
        sys.exit(0)

    # Execute
    print()
    print("Creating admin site...")
    site_data = {
        'name': admin_site_name,
        'domain': admin_domain,
        'frontend_url': frontend_url,
        'email_from': email_from,
        'email_from_name': email_from_name,
        'allow_self_registration': False
    }

    try:
        site = create_site(api_url, api_key, site_data)
        print(f"  ✓ Admin site created (ID: {site['id']})")

        print("Creating admin user...")
        user = create_admin_user(api_url, api_key, site['id'], admin_email)
        print(f"  ✓ Admin user created (ID: {user['id']})")

        print()
        print("=" * 60)
        print("Bootstrap Complete!")
        print("=" * 60)
        print()
        print("Next steps:")
        print(f"  1. Check {admin_email} for a verification email")
        print(f"  2. Click the link to set your password and verify your account")
        print(f"  3. Add this to your frontend .env.local:")
        print(f"     AEGIS_ADMIN_DOMAIN={admin_domain}")
        print(f"  4. Navigate to your Aegis frontend's /aegis-admin/login page")
        print(f"  5. Login with {admin_email} and the password you set")
        print()

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
