#!/usr/bin/env python
"""
Interactive script to update an existing site in the auth service.
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
        choice = input(f"\nSelect a site to update (1-{len(sites)}): ").strip()
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
    print("Update Site - Multi-Tenant Auth Service")
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

    print()
    print("=" * 60)
    print(f"Current Site Details (ID: {selected_site['id']})")
    print("=" * 60)
    print(f"  Name: {selected_site['name']}")
    print(f"  Domain: {selected_site['domain']}")
    print(f"  Frontend URL: {selected_site['frontend_url']}")
    print(f"  Verification Redirect: {selected_site.get('verification_redirect_url') or '(not set)'}")
    print(f"  Email From: {selected_site['email_from']}")
    print(f"  Email From Name: {selected_site['email_from_name']}")
    print(f"  Webhook URL: {selected_site.get('webhook_url') or '(not set)'}")
    print(f"  Webhook Secret: {selected_site.get('webhook_secret') or '(not set)'}")
    print("=" * 60)

    print()
    print("Enter new values (press Enter to keep current value):")
    print("-" * 60)

    # Collect updated values - use current values as defaults
    update_data = {}

    new_name = get_input("Site name", selected_site['name'])
    if new_name != selected_site['name']:
        update_data['name'] = new_name

    new_domain = get_input("Domain", selected_site['domain'])
    if new_domain != selected_site['domain']:
        update_data['domain'] = new_domain

    new_frontend_url = get_input("Frontend URL", selected_site['frontend_url'])
    if new_frontend_url != selected_site['frontend_url']:
        update_data['frontend_url'] = new_frontend_url

    current_redirect = selected_site.get('verification_redirect_url') or ""
    print()
    print("Verification redirect URL (where users go after email verification).")
    print("Leave blank to use frontend URL, or enter a welcome/thank-you page URL.")
    new_redirect = get_input(
        "Verification redirect URL",
        current_redirect if current_redirect else None,
        required=False
    )
    if new_redirect != current_redirect:
        update_data['verification_redirect_url'] = new_redirect if new_redirect else None

    new_email_from = get_input("Email from address", selected_site['email_from'])
    if new_email_from != selected_site['email_from']:
        update_data['email_from'] = new_email_from

    new_email_from_name = get_input("Email from name", selected_site['email_from_name'])
    if new_email_from_name != selected_site['email_from_name']:
        update_data['email_from_name'] = new_email_from_name

    current_webhook = selected_site.get('webhook_url') or ""
    print()
    print("Webhook URL (receives POST notifications for auth events).")
    print("Leave blank to clear, or enter a URL.")
    new_webhook = get_input(
        "Webhook URL",
        current_webhook if current_webhook else None,
        required=False
    )
    if new_webhook != current_webhook:
        update_data['webhook_url'] = new_webhook if new_webhook else None

    if current_webhook and new_webhook == current_webhook:
        regen = input("Regenerate webhook secret? (y/n) [n]: ").strip().lower()
        if regen in ('y', 'yes'):
            update_data['regenerate_webhook_secret'] = True

    # Check if any changes were made
    if not update_data:
        print("\nNo changes made.")
        sys.exit(0)

    print()
    print("-" * 60)
    print("Changes to apply:")
    print("-" * 60)
    for key, value in update_data.items():
        display_value = value if value is not None else "(clear)"
        print(f"  {key}: {display_value}")
    print("-" * 60)

    # Confirm
    confirm = input("\nApply these changes? (y/n): ").strip().lower()
    if confirm not in ('y', 'yes'):
        print("Cancelled.")
        sys.exit(0)

    # Make API request
    print("\nUpdating site...")
    try:
        response = requests.put(
            f"{api_url}/api/sites/{selected_site['id']}",
            headers={
                'X-API-Key': api_key,
                'Content-Type': 'application/json'
            },
            json=update_data
        )

        if response.status_code == 200:
            site = response.json()
            print("\n✓ Site updated successfully!")
            print("=" * 60)
            print(f"Site ID: {site['id']}")
            print(f"Name: {site['name']}")
            print(f"Domain: {site['domain']}")
            print(f"Frontend URL: {site['frontend_url']}")
            if site.get('verification_redirect_url'):
                print(f"Verification Redirect: {site['verification_redirect_url']}")
            print(f"Email From: {site['email_from_name']} <{site['email_from']}>")
            if site.get('webhook_url'):
                print(f"Webhook URL: {site['webhook_url']}")
                print(f"Webhook Secret: {site.get('webhook_secret', '(not returned)')}")
            print(f"Updated: {site['updated_at']}")
            print("=" * 60)
        else:
            print(f"\n✗ Error updating site (HTTP {response.status_code}):")
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
