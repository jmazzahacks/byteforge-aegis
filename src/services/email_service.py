"""
Email service for sending transactional emails using Mailgun.
"""
import requests
from config import get_config
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class EmailService:
    """Service for sending emails via Mailgun"""

    def __init__(self):
        """Initialize the email service with Mailgun API key and domain from config"""
        config = get_config()
        self.api_key = config.MAILGUN_API_KEY
        self.domain = config.MAILGUN_DOMAIN
        self.api_url = f"https://api.mailgun.net/v3/{self.domain}/messages"
        self.aegis_frontend_url = config.AEGIS_FRONTEND_URL.rstrip('/')

    def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        from_email: str,
        from_name: str,
        text_content: Optional[str] = None
    ) -> bool:
        """
        Send an email using Mailgun.

        Args:
            to_email: Recipient email address
            subject: Email subject
            html_content: HTML content of the email
            from_email: Sender email address (site-specific)
            from_name: Sender display name (site-specific)
            text_content: Plain text content (optional)

        Returns:
            bool: True if email sent successfully, False otherwise
        """
        if not self.api_key:
            logger.error("Mailgun API key not configured")
            return False

        if not self.domain:
            logger.error("Mailgun domain not configured")
            return False

        logger.info(f"Attempting to send email to {to_email} from {from_email}")
        logger.debug(f"Subject: {subject}")

        try:
            # Format the from address with name
            from_address = f"{from_name} <{from_email}>"

            # Prepare the email data
            data = {
                "from": from_address,
                "to": to_email,
                "subject": subject,
                "html": html_content
            }

            # Add text content if provided
            if text_content:
                data["text"] = text_content

            # Send the request to Mailgun
            response = requests.post(
                self.api_url,
                auth=("api", self.api_key),
                data=data,
                timeout=10
            )

            if response.status_code == 200:
                logger.info(f"✓ Email sent successfully to {to_email} (Status: {response.status_code})")
                logger.debug(f"Mailgun response: {response.json()}")
                return True
            else:
                logger.error(f"✗ Failed to send email. Status: {response.status_code}, Body: {response.text}")
                return False

        except requests.exceptions.Timeout:
            logger.error(f"✗ Timeout sending email to {to_email}", exc_info=True)
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"✗ Request error sending email to {to_email}: {str(e)}", exc_info=True)
            return False
        except Exception as e:
            logger.error(f"✗ Error sending email to {to_email}: {str(e)}", exc_info=True)
            return False

    def send_verification_email(
        self,
        to_email: str,
        token: str,
        site_name: str,
        frontend_url: str,
        from_email: str,
        from_name: str
    ) -> bool:
        """
        Send email verification email.

        Args:
            to_email: User's email address
            token: Verification token
            site_name: Name of the site
            frontend_url: Frontend URL for this site (verification happens here)
            from_email: Sender email address (site-specific)
            from_name: Sender display name (site-specific)

        Returns:
            bool: True if sent successfully
        """
        # Use site's frontend URL for verification (tenant handles verification)
        base_url = frontend_url.rstrip('/')
        verification_url = f"{base_url}/verify-email?token={token}"

        subject = f"Verify your email for {site_name}"

        html_content = f"""
        <html>
            <body>
                <h2>Welcome to {site_name}!</h2>
                <p>Please verify your email address by clicking the link below:</p>
                <p><a href="{verification_url}">Verify Email</a></p>
                <p>Or copy and paste this URL into your browser:</p>
                <p>{verification_url}</p>
                <p>This link will expire in 24 hours.</p>
                <p>If you didn't create an account, you can safely ignore this email.</p>
            </body>
        </html>
        """

        text_content = f"""
        Welcome to {site_name}!

        Please verify your email address by visiting this URL:
        {verification_url}

        This link will expire in 24 hours.

        If you didn't create an account, you can safely ignore this email.
        """

        return self.send_email(to_email, subject, html_content, from_email, from_name, text_content)

    def send_password_reset_email(
        self,
        to_email: str,
        token: str,
        site_name: str,
        frontend_url: str,
        from_email: str,
        from_name: str
    ) -> bool:
        """
        Send password reset email.

        Args:
            to_email: User's email address
            token: Password reset token
            site_name: Name of the site
            frontend_url: Frontend URL for this site
            from_email: Sender email address (site-specific)
            from_name: Sender display name (site-specific)

        Returns:
            bool: True if sent successfully
        """
        # Strip trailing slash from frontend_url to avoid double slashes
        base_url = frontend_url.rstrip('/')
        reset_url = f"{base_url}/reset-password?token={token}"

        subject = f"Reset your password for {site_name}"

        html_content = f"""
        <html>
            <body>
                <h2>Password Reset Request</h2>
                <p>We received a request to reset your password for {site_name}.</p>
                <p>Click the link below to reset your password:</p>
                <p><a href="{reset_url}">Reset Password</a></p>
                <p>Or copy and paste this URL into your browser:</p>
                <p>{reset_url}</p>
                <p>This link will expire in 1 hour.</p>
                <p>If you didn't request a password reset, you can safely ignore this email.</p>
            </body>
        </html>
        """

        text_content = f"""
        Password Reset Request

        We received a request to reset your password for {site_name}.

        Visit this URL to reset your password:
        {reset_url}

        This link will expire in 1 hour.

        If you didn't request a password reset, you can safely ignore this email.
        """

        return self.send_email(to_email, subject, html_content, from_email, from_name, text_content)

    def send_email_change_confirmation(
        self,
        to_email: str,
        token: str,
        site_name: str,
        frontend_url: str,
        from_email: str,
        from_name: str
    ) -> bool:
        """
        Send email change confirmation email.

        Args:
            to_email: New email address
            token: Email change confirmation token
            site_name: Name of the site
            frontend_url: Frontend URL for this site
            from_email: Sender email address (site-specific)
            from_name: Sender display name (site-specific)

        Returns:
            bool: True if sent successfully
        """
        # Strip trailing slash from frontend_url to avoid double slashes
        base_url = frontend_url.rstrip('/')
        confirmation_url = f"{base_url}/confirm-email-change?token={token}"

        subject = f"Confirm your email change for {site_name}"

        html_content = f"""
        <html>
            <body>
                <h2>Email Change Confirmation</h2>
                <p>You requested to change your email address for {site_name}.</p>
                <p>Click the link below to confirm this change:</p>
                <p><a href="{confirmation_url}">Confirm Email Change</a></p>
                <p>Or copy and paste this URL into your browser:</p>
                <p>{confirmation_url}</p>
                <p>This link will expire in 24 hours.</p>
                <p>If you didn't request this change, please ignore this email and contact support immediately.</p>
            </body>
        </html>
        """

        text_content = f"""
        Email Change Confirmation

        You requested to change your email address for {site_name}.

        Visit this URL to confirm this change:
        {confirmation_url}

        This link will expire in 24 hours.

        If you didn't request this change, please ignore this email and contact support immediately.
        """

        return self.send_email(to_email, subject, html_content, from_email, from_name, text_content)


    def send_registration_attempt_email(
        self,
        to_email: str,
        site_name: str,
        frontend_url: str,
        from_email: str,
        from_name: str
    ) -> bool:
        """
        Send notification when someone attempts to register with an existing email.

        This is a security measure to prevent email enumeration while still
        notifying the legitimate account owner.

        Args:
            to_email: Existing user's email address
            site_name: Name of the site
            frontend_url: Frontend URL for this site
            from_email: Sender email address (site-specific)
            from_name: Sender display name (site-specific)

        Returns:
            bool: True if sent successfully
        """
        base_url = frontend_url.rstrip('/')
        login_url = f"{base_url}/login"
        reset_url = f"{base_url}/forgot-password"

        subject = f"Sign-in attempt for {site_name}"

        html_content = f"""
        <html>
            <body>
                <h2>Account Already Exists</h2>
                <p>Someone attempted to create a new account on {site_name} using this email address.</p>
                <p>If this was you and you forgot you already have an account, you can:</p>
                <ul>
                    <li><a href="{login_url}">Sign in to your existing account</a></li>
                    <li><a href="{reset_url}">Reset your password</a> if you've forgotten it</li>
                </ul>
                <p>If this wasn't you, someone may have mistyped their email address. No action is needed.</p>
                <p>Your account remains secure and unchanged.</p>
            </body>
        </html>
        """

        text_content = f"""
        Account Already Exists

        Someone attempted to create a new account on {site_name} using this email address.

        If this was you and you forgot you already have an account, you can:
        - Sign in: {login_url}
        - Reset password: {reset_url}

        If this wasn't you, someone may have mistyped their email address. No action is needed.

        Your account remains secure and unchanged.
        """

        return self.send_email(to_email, subject, html_content, from_email, from_name, text_content)


# Singleton instance
email_service = EmailService()
