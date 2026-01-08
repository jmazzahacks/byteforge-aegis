#!/usr/bin/env python
"""
Interactive script to create a new user (regular or admin) for a site.
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


def list_sites(api_url: str, api_key: str) -> list:
    """Fetch and display all sites"""
    try:
        response = requests.get(
            f"{api_url}/api/sites",
            headers={'X-API-Key': api_key}
        )

        if response.status_code == 200:
            return response.json()
        else:
            print(f"\n✗ Error fetching sites (HTTP {response.status_code}):")
            print(response.json())
            sys.exit(1)
    except requests.exceptions.ConnectionError:
        print(f"\n✗ Error: Could not connect to {api_url}")
        print("Is the auth service running?")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        sys.exit(1)


def select_site(sites: list) -> dict:
    """Display sites and let user select one"""
    if not sites:
        print("\n✗ No sites available. Create a site first.")
        sys.exit(1)

    print("\nAvailable Sites:")
    print("=" * 60)
    for idx, site in enumerate(sites, 1):
        print(f"{idx}. {site['name']} ({site['domain']})")
        print(f"   ID: {site['id']}")
    print("=" * 60)

    while True:
        choice = input(f"\nSelect a site (1-{len(sites)}): ").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(sites):
                return sites[idx]
            else:
                print(f"Please enter a number between 1 and {len(sites)}")
        except ValueError:
            print("Please enter a valid number")


def main():
    print("=" * 60)
    print("Create New User - Multi-Tenant Auth Service")
    print("=" * 60)
    print()

    # Get API configuration
    api_url = get_input("Auth service URL", "http://127.0.0.1:5678")
    api_key = os.getenv('MASTER_API_KEY')

    if not api_key:
        api_key = get_input("Master API Key (or set MASTER_API_KEY in .env)")

    # Fetch and select site
    sites = list_sites(api_url, api_key)
    selected_site = select_site(sites)

    print(f"\nSelected site: {selected_site['name']} (ID: {selected_site['id']})")
    print("-" * 60)

    # Ask if creating admin or regular user
    print("\nUser Type:")
    print("1. Regular User (role: user)")
    print("2. Site Administrator (role: admin)")

    while True:
        user_type = input("Select user type (1-2): ").strip()
        if user_type in ('1', '2'):
            is_admin = (user_type == '2')
            break
        print("Please enter 1 or 2")

    role = "admin" if is_admin else "user"
    role_name = "Admin" if is_admin else "Regular User"
    print(f"\nCreating {role_name}")
    print("-" * 60)

    # Collect user information
    email = get_input("Email address")

    print()
    print("-" * 60)
    print(f"User details:")
    print(f"  Site: {selected_site['name']} ({selected_site['domain']})")
    print(f"  Email: {email}")
    print(f"  Role: {role_name}")
    print("-" * 60)
    print("\nNote: User will set their own password via email verification.")

    # Confirm
    confirm = input("\nCreate this user? (y/n): ").strip().lower()
    if confirm not in ('y', 'yes'):
        print("Cancelled.")
        sys.exit(0)

    # Use admin register endpoint (user sets password via email)
    endpoint = f"{api_url}/api/admin/register"
    headers = {
        'X-API-Key': api_key,
        'Content-Type': 'application/json'
    }

    user_data = {
        'site_id': selected_site['id'],
        'email': email,
        'role': role
    }

    # Make API request
    print("\nCreating user...")
    try:
        response = requests.post(endpoint, headers=headers, json=user_data)

        if response.status_code == 201:
            user = response.json()
            print("\n✓ User created successfully!")
            print("=" * 60)
            print(f"User ID: {user['id']}")
            print(f"Email: {user['email']}")
            print(f"Role: {user['role']}")
            print(f"Site: {selected_site['name']}")
            print(f"Verified: {user['is_verified']}")
            print(f"Created: {user['created_at']}")
            print("=" * 60)
            print("\nNote: User will receive an email to set their password and verify.")
            print("They must complete this step before logging in.")
        else:
            print(f"\n✗ Error creating user (HTTP {response.status_code}):")
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
