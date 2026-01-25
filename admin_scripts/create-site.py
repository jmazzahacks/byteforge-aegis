#!/usr/bin/env python
"""
Interactive script to create a new site in the auth service.
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


def main():
    print("=" * 60)
    print("Create New Site - Multi-Tenant Auth Service")
    print("=" * 60)
    print()

    # Get API configuration
    api_url = get_input("Auth service URL", "http://127.0.0.1:5678")
    api_key = os.getenv('MASTER_API_KEY')

    if not api_key:
        api_key = get_input("Master API Key (or set MASTER_API_KEY in .env)")

    print()
    print("Enter site details:")
    print("-" * 60)

    # Collect site information
    site_data = {
        'name': get_input("Site name (e.g., 'My Website')"),
        'domain': get_input("Domain (e.g., 'example.com')"),
        'frontend_url': get_input("Frontend URL (e.g., 'https://example.com')"),
        'email_from': get_input("Email from address (e.g., 'noreply@example.com')"),
        'email_from_name': get_input("Email from name (e.g., 'My Website')")
    }

    # Optional: verification redirect URL
    print()
    print("After email verification, users will be redirected to a URL.")
    print("Leave blank to use the frontend URL, or specify a welcome/thank-you page.")
    verification_redirect = get_input(
        "Verification redirect URL (e.g., 'https://example.com/welcome')",
        required=False
    )
    if verification_redirect:
        site_data['verification_redirect_url'] = verification_redirect

    # Optional: self-registration setting
    print()
    print("Self-registration allows users to register via the public /api/auth/register endpoint.")
    print("Disabling it means users can only be created by admins.")
    allow_self_reg = get_input("Allow self-registration? (y/n)", "y")
    site_data['allow_self_registration'] = allow_self_reg.lower() in ('y', 'yes')

    print()
    print("-" * 60)
    print("Site details to create:")
    print("-" * 60)
    for key, value in site_data.items():
        print(f"  {key}: {value}")
    print("-" * 60)

    # Confirm
    confirm = input("\nCreate this site? (y/n): ").strip().lower()
    if confirm not in ('y', 'yes'):
        print("Cancelled.")
        sys.exit(0)

    # Make API request
    print("\nCreating site...")
    try:
        response = requests.post(
            f"{api_url}/api/sites",
            headers={
                'X-API-Key': api_key,
                'Content-Type': 'application/json'
            },
            json=site_data
        )

        if response.status_code == 201:
            site = response.json()
            print("\n✓ Site created successfully!")
            print("=" * 60)
            print(f"Site ID: {site['id']}")
            print(f"Name: {site['name']}")
            print(f"Domain: {site['domain']}")
            print(f"Frontend URL: {site['frontend_url']}")
            if site.get('verification_redirect_url'):
                print(f"Verification Redirect: {site['verification_redirect_url']}")
            self_reg = "Enabled" if site.get('allow_self_registration', True) else "Disabled"
            print(f"Self-Registration: {self_reg}")
            print(f"Email From: {site['email_from_name']} <{site['email_from']}>")
            print(f"Created: {site['created_at']}")
            print("=" * 60)
            print(f"\nUse site_id={site['id']} in your frontend configuration.")
        else:
            print(f"\n✗ Error creating site (HTTP {response.status_code}):")
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
