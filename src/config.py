import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Base configuration"""
    # Flask SECRET_KEY - Not currently used (stateless token-based auth, no sessions/cookies)
    # Reserved for future use if sessions, CSRF, or signed cookies are needed
    SECRET_KEY: str = os.getenv('SECRET_KEY', 'dev-secret-key')
    # Master API key for administrative operations (site creation/management)
    MASTER_API_KEY: str = os.getenv('MASTER_API_KEY', '')

    # Database
    DB_HOST: str = os.getenv('DB_HOST', 'localhost')
    DB_PORT: int = int(os.getenv('DB_PORT', 5432))
    DB_NAME: str = os.getenv('DB_NAME', 'aegis')
    DB_USER: str = os.getenv('DB_USER', 'aegis_admin')
    DB_PASSWORD: str = os.getenv('DB_PASSWORD', 'aegis_admin')

    # Mailgun
    MAILGUN_API_KEY: str = os.getenv('MAILGUN_API_KEY', '')
    MAILGUN_DOMAIN: str = os.getenv('MAILGUN_DOMAIN', '')
    EMAIL_FROM: str = os.getenv('EMAIL_FROM', 'noreply@example.com')
    EMAIL_FROM_NAME: str = os.getenv('EMAIL_FROM_NAME', 'ByteForge Aegis')

    # Token Expiration (seconds)
    AUTH_TOKEN_EXPIRATION: int = int(os.getenv('AUTH_TOKEN_EXPIRATION', 3600))
    EMAIL_VERIFICATION_EXPIRATION: int = int(os.getenv('EMAIL_VERIFICATION_EXPIRATION', 86400))
    PASSWORD_RESET_EXPIRATION: int = int(os.getenv('PASSWORD_RESET_EXPIRATION', 3600))
    EMAIL_CHANGE_EXPIRATION: int = int(os.getenv('EMAIL_CHANGE_EXPIRATION', 3600))

    # Refresh Token Configuration
    REFRESH_TOKEN_EXPIRATION: int = int(os.getenv('REFRESH_TOKEN_EXPIRATION', 604800))  # 7 days
    REFRESH_TOKEN_ROTATION: bool = os.getenv('REFRESH_TOKEN_ROTATION', 'true').lower() == 'true'
    REFRESH_TOKEN_GRACE_PERIOD: int = int(os.getenv('REFRESH_TOKEN_GRACE_PERIOD', 30))  # seconds

    # Application
    APP_HOST: str = os.getenv('APP_HOST', '0.0.0.0')
    APP_PORT: int = int(os.getenv('APP_PORT', 5678))

    # Aegis Frontend URL (for centralized email verification links)
    AEGIS_FRONTEND_URL: str = os.getenv('AEGIS_FRONTEND_URL', 'http://localhost:3000')


class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG: bool = True
    FLASK_ENV: str = 'development'


class ProductionConfig(Config):
    """Production configuration"""
    DEBUG: bool = False
    FLASK_ENV: str = 'production'


def get_config() -> Config:
    """Get configuration based on environment"""
    env = os.getenv('FLASK_ENV', 'development')

    if env == 'production':
        return ProductionConfig()

    return DevelopmentConfig()
