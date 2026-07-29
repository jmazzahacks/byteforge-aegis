import logging
import os
import re
import time

from flask import Flask, g, request
from byteforge_loki_logging import configure_logging

from config import get_config
from utils.cors_origins import allowed_origins
from utils.rate_limit import (
    LOGIN_IP_LIMIT,
    LOGIN_LIMIT,
    PASSWORD_RESET_IP_LIMIT,
    PASSWORD_RESET_LIMIT,
    PASSWORD_RESET_SITE_LIMIT,
    REGISTER_IP_LIMIT,
    REGISTER_LIMIT,
    client_ip_key,
    client_ip_unavailable,
    limiter,
    site_email_key,
    site_key,
)

# Requests at or above this take a WARNING rather than INFO, so slow queries
# surface without having to grep every access line.
SLOW_REQUEST_MS = 1000

# Guards on the fallback route label for unmatched requests: control
# characters are what allow a forged log line, and the length cap stops a
# single request filling the log.
_UNSAFE_LOG_CHARS = re.compile(r'[\x00-\x1f\x7f]')
MAX_LOGGED_PATH_CHARS = 200


def create_app() -> Flask:
    """Application factory pattern"""
    # Configure logging inside create_app() so it runs post-fork in the
    # gunicorn worker process. Module-level init causes SSL context issues
    # with the Loki handler because the SSL session doesn't survive fork().
    debug_mode = os.environ.get('DEBUG_LOCAL', 'true').lower() == 'true'
    log_level = os.environ.get('LOG_LEVEL', 'INFO')
    configure_logging(
        application_tag='aegis-backend',
        debug_local=debug_mode,
        local_level=log_level,
    )

    app = Flask(__name__)

    # Force Flask, werkzeug, and gunicorn loggers to propagate to root.
    # These loggers create their own StreamHandlers with propagate=False,
    # which bypasses the root logger's Loki handler.
    app.logger.handlers.clear()
    app.logger.propagate = True
    app.logger.setLevel(logging.DEBUG)

    for name in ('werkzeug', 'gunicorn', 'gunicorn.error', 'gunicorn.access'):
        dep_logger = logging.getLogger(name)
        dep_logger.handlers.clear()
        dep_logger.propagate = True

    # Load configuration
    config = get_config()
    app.config.from_object(config)

    # CORS is restricted to the tenants' own frontends, derived from the
    # sites table. It used to be '*' — and env.docker.example shipped that
    # value, so the wildcard was the deployed policy, not a dev default.
    # That let any web page drive this auth API from a visitor's browser and
    # read the response, which combined with per-account (rather than
    # per-IP) rate limits is a distributed credential-stuffing surface
    # sourced from real residential addresses.
    #
    # CORS_ORIGINS still wins when set, including the literal '*', so an
    # operator can override without a code change. Otherwise the list comes
    # from the database and refreshes on a TTL, so a new tenant works
    # without a restart.
    #
    # Handled here rather than with flask-cors because that library resolves
    # its options once, at init. An allow-list that changes while the process
    # runs cannot be expressed through it — updating app.config per request
    # is silently ignored, and the failure mode looks like success: every
    # origin is echoed back, including ones that should have been refused.
    #
    # No Access-Control-Allow-Credentials: this API is bearer-authenticated
    # with no cookies, so the browser has no ambient credential to attach.
    cors_override = os.getenv('CORS_ORIGINS')

    @app.after_request
    def _apply_cors(response):
        origin = request.headers.get('Origin')
        if not origin:
            return response

        if cors_override == '*':
            permitted = True
        elif cors_override:
            permitted = origin in [o.strip() for o in cors_override.split(',')]
        else:
            permitted = origin in allowed_origins()

        if not permitted:
            return response

        response.headers['Access-Control-Allow-Origin'] = origin
        # Without Vary, a shared cache could serve one tenant's allowed
        # response to a different origin.
        response.headers.add('Vary', 'Origin')

        if request.method == 'OPTIONS':
            response.headers['Access-Control-Allow-Methods'] = (
                'GET, POST, PUT, DELETE, OPTIONS'
            )
            response.headers['Access-Control-Allow-Headers'] = (
                'Content-Type, Authorization, X-API-Key, X-Tenant-Api-Key'
            )
            response.headers['Access-Control-Max-Age'] = '600'

        return response

    # Register blueprints
    from api.register import register_bp
    from api.admin_register import admin_register_bp
    from api.login import login_bp
    from api.logout import logout_bp
    from api.refresh_token import refresh_token_bp
    from api.verify_email import verify_email_bp
    from api.check_verification_token import check_verification_token_bp
    from api.change_password import change_password_bp
    from api.request_password_reset import request_password_reset_bp
    from api.reset_password import reset_password_bp
    from api.request_email_change import request_email_change_bp
    from api.confirm_email_change import confirm_email_change_bp
    from api.create_site import create_site_bp
    from api.get_site import get_site_bp
    from api.list_sites import list_sites_bp
    from api.update_site import update_site_bp
    from api.delete_site import delete_site_bp
    from api.list_users import list_users_bp
    from api.resend_verification import resend_verification_bp
    from api.delete_user import delete_user_bp
    from api.update_user import update_user_bp
    from api.admin_list_users import admin_list_users_bp
    from api.admin_register_user import admin_register_user_bp
    from api.me import me_bp
    from api.get_user import get_user_bp
    from api.cleanup_expired_tokens import cleanup_expired_tokens_bp

    app.register_blueprint(register_bp)
    app.register_blueprint(admin_register_bp)
    app.register_blueprint(login_bp)
    app.register_blueprint(logout_bp)
    app.register_blueprint(refresh_token_bp)
    app.register_blueprint(verify_email_bp)
    app.register_blueprint(check_verification_token_bp)
    app.register_blueprint(change_password_bp)
    app.register_blueprint(request_password_reset_bp)
    app.register_blueprint(reset_password_bp)
    app.register_blueprint(request_email_change_bp)
    app.register_blueprint(confirm_email_change_bp)
    app.register_blueprint(create_site_bp)
    app.register_blueprint(get_site_bp)
    app.register_blueprint(list_sites_bp)
    app.register_blueprint(update_site_bp)
    app.register_blueprint(delete_site_bp)
    app.register_blueprint(list_users_bp)
    app.register_blueprint(resend_verification_bp)
    app.register_blueprint(delete_user_bp)
    app.register_blueprint(update_user_bp)
    app.register_blueprint(admin_list_users_bp)
    app.register_blueprint(admin_register_user_bp)
    app.register_blueprint(me_bp)
    app.register_blueprint(get_user_bp)
    app.register_blueprint(cleanup_expired_tokens_bp)

    # Applied to blueprints rather than as decorators on the views: each
    # endpoint here is its own blueprint, and importing the limiter into
    # every api module to decorate would make the import graph circular.
    limiter.init_app(app)
    limiter.limit(LOGIN_LIMIT, key_func=site_email_key)(login_bp)
    limiter.limit(REGISTER_LIMIT, key_func=site_email_key)(register_bp)
    limiter.limit(PASSWORD_RESET_LIMIT, key_func=site_email_key)(request_password_reset_bp)
    limiter.limit(PASSWORD_RESET_SITE_LIMIT, key_func=site_key)(request_password_reset_bp)

    # Per-IP, on top of the per-account limits above. These catch a spread
    # attack — one guess each against many accounts, which stays under every
    # per-account limit. They exempt themselves when no trustworthy client
    # address is available rather than collapsing every caller into one
    # bucket, which would take logins down for every tenant at once.
    limiter.limit(LOGIN_IP_LIMIT, key_func=client_ip_key,
                  exempt_when=client_ip_unavailable)(login_bp)
    limiter.limit(REGISTER_IP_LIMIT, key_func=client_ip_key,
                  exempt_when=client_ip_unavailable)(register_bp)
    limiter.limit(PASSWORD_RESET_IP_LIMIT, key_func=client_ip_key,
                  exempt_when=client_ip_unavailable)(request_password_reset_bp)

    # Health check endpoint
    logger = logging.getLogger(__name__)

    @app.route('/api/health', methods=['GET'])
    def health_check():
        logger.info('Health check', extra={'route': '/api/health'})
        return {'status': 'healthy', 'service': 'auth-service'}, 200

    # Access logging. Until this existed we logged nothing but health checks,
    # so a latency question about our own service could only be answered by
    # reading the gunicorn config (hivemake ticket cc296d7c, 2026-07-28).
    #
    # NOTE ON WHAT THIS DOES NOT MEASURE: duration is handler time only. Under
    # gthread the connection is accepted and parsed before being handed to the
    # thread pool, so time spent waiting for a free thread elapses before the
    # WSGI callable runs and is invisible here. Diagnosing queueing needs the
    # reverse proxy to stamp request-start and log the delta.
    @app.before_request
    def _start_request_timer() -> None:
        g.request_started_at = time.perf_counter()

    @app.after_request
    def _log_request(response):
        started_at = g.pop('request_started_at', None)
        if started_at is None or request.path == '/api/health':
            return response

        duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
        level = logging.WARNING if duration_ms >= SLOW_REQUEST_MS else logging.INFO
        logger.log(
            level,
            '%s %s %s %sms',
            request.method, _log_safe_route(), response.status_code, duration_ms,
            extra={
                'route': _log_safe_route(),
                'method': request.method,
                'status': response.status_code,
                'duration_ms': duration_ms,
            },
        )
        return response

    return app


def _log_safe_route() -> str:
    """A route label safe to put in a log line.

    Prefers the matched rule (`/api/sites/<site_id>/users`), which is a fixed
    template: not attacker-controlled, and it keeps user and site UUIDs out of
    the logs. Unmatched requests (404s, which need no authentication) have no
    rule, so their raw path is sanitized instead — it is percent-decoded by
    the server, so it can carry newlines, and an attacker could otherwise
    inject entire well-formed log entries (a fabricated successful login, say)
    into the stdout log, whose formatter does no escaping.
    """
    if request.url_rule is not None:
        return str(request.url_rule)

    path = request.path[:MAX_LOGGED_PATH_CHARS]
    return _UNSAFE_LOG_CHARS.sub('?', path)


if __name__ == '__main__':
    app = create_app()
    config = get_config()
    app.run(host=config.APP_HOST, port=config.APP_PORT, debug=config.DEBUG)
