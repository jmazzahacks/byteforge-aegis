from dataclasses import dataclass


@dataclass
class TokenCleanupResult:
    """
    Row counts removed by one expired-token cleanup pass.

    Reported per table rather than as a single number so an operator can tell
    a healthy steady state from a symptom: auth tokens churning while refresh
    tokens sit still is normal, but a password-reset table that never drains
    means something is minting resets nobody is consuming.

    Attributes:
        auth_tokens: Expired auth tokens deleted
        refresh_tokens: Expired refresh tokens deleted
        email_verification_tokens: Expired email verification tokens deleted
        password_reset_tokens: Expired password reset tokens deleted
        email_change_requests: Expired email change requests deleted
    """
    auth_tokens: int
    refresh_tokens: int
    email_verification_tokens: int
    password_reset_tokens: int
    email_change_requests: int

    @property
    def total(self) -> int:
        """Total rows removed across all five token tables."""
        return (
            self.auth_tokens
            + self.refresh_tokens
            + self.email_verification_tokens
            + self.password_reset_tokens
            + self.email_change_requests
        )
