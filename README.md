# ByteForge Aegis

Multi-tenant authentication service built with Flask and PostgreSQL. Provides secure user authentication, email verification, password management, and role-based authorization for multiple sites with isolated user bases.

## Features

- **Multi-tenant Architecture** - Isolate users and authentication by site/domain
- **Per-Site Registration Control** - Enable or disable self-registration per tenant
- **User Management** - Registration, login, email verification
- **Centralized Email Verification** - Single verification page handles all tenants with configurable redirect
- **Password Management** - Password changes, reset via email
- **Email Management** - Change email with verification
- **Role-Based Authorization** - User and admin roles per site
- **API Key Authentication** - Master API key for administrative operations
- **Email Integration** - Mailgun integration for transactional emails
- **Token-Based Sessions** - Secure authentication tokens with expiration
- **Refresh Tokens** - Long-lived refresh tokens for seamless session extension
- **PostgreSQL Backend** - Reliable data storage with proper indexing

## Requirements

- Python 3.13+
- PostgreSQL 12+
- Mailgun account (for email functionality)

## Security Notice

⚠️ **Never use default configuration values in production!**

All configuration must be set via environment variables in production environments:
- Generate a strong random `SECRET_KEY`
- Set a strong `MASTER_API_KEY`
- Use a secure `DB_PASSWORD`
- Configure your `MAILGUN_API_KEY` and `MAILGUN_DOMAIN`

See `.env.example` for all required variables.

## Quick Start

1. **Clone the repository**
   ```bash
   git clone https://github.com/jmazzahacks/byteforge-aegis.git
   cd byteforge-aegis
   ```

2. **Set up Python virtual environment** (already included)
   ```bash
   # Virtual environment is included in the repository
   # Activate it for all commands
   source bin/activate
   ```

3. **Configure environment variables**
   ```bash
   cp .env.example .env
   # Edit .env and configure your settings
   ```

4. **Set up PostgreSQL database**
   ```bash
   # Create database and user in PostgreSQL
   psql -U postgres -c "CREATE DATABASE aegis;"
   psql -U postgres -c "CREATE USER aegis_admin WITH PASSWORD 'your-secure-password';"
   psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE aegis TO aegis_admin;"

   # Load schema
   psql -U aegis_admin -d aegis -f database/schema.sql
   ```

5. **Install dependencies**
   ```bash
   source bin/activate && pip install -r requirements.txt
   ```

6. **Run the application**
   ```bash
   source bin/activate && python src/app.py
   ```

The API will be available at `http://localhost:5678`

## API Documentation

### Site Management

Site management endpoints require the master API key (`X-API-Key` header).

#### Create Site

Create a new tenant site. Use the included interactive script for convenience.

```bash
# Using the interactive script (Docker)
docker exec -it aegis python admin_scripts/create-site.py

# Using the interactive script (local development)
source bin/activate && python admin_scripts/create-site.py

# Or using curl directly
curl -X POST http://localhost:5678/api/sites \
  -H "X-API-Key: your-master-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Website",
    "domain": "example.com",
    "frontend_url": "https://example.com",
    "verification_redirect_url": "https://example.com/welcome",
    "email_from": "noreply@example.com",
    "email_from_name": "My Website",
    "allow_self_registration": true
  }'
```

**Optional fields:**
- `verification_redirect_url` - If not set, users redirect to `frontend_url` after email verification
- `allow_self_registration` - Defaults to `true`. Set to `false` to disable public registration (admin registration still works)

#### List All Sites

```bash
curl http://localhost:5678/api/sites \
  -H "X-API-Key: your-master-api-key"
```

#### Get Site by ID

```bash
curl http://localhost:5678/api/sites/{site_id} \
  -H "X-API-Key: your-master-api-key"
```

#### Get Site by Domain (Public)

This endpoint is public to allow frontend applications to bootstrap by looking up their site configuration.

```bash
curl "http://localhost:5678/api/sites/by-domain?domain=example.com"
```

#### Update Site

All fields are optional. Only provided fields will be updated.

```bash
# Using the interactive script (Docker)
docker exec -it aegis python admin_scripts/update-site.py

# Using the interactive script (local development)
source bin/activate && python admin_scripts/update-site.py

# Or using curl directly
curl -X PUT http://localhost:5678/api/sites/{site_id} \
  -H "X-API-Key: your-master-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Updated Name",
    "verification_redirect_url": "https://example.com/welcome",
    "email_from": "support@example.com",
    "email_from_name": "Support Team"
  }'
```

**Disable self-registration for a site:**
```bash
curl -X PUT http://localhost:5678/api/sites/{site_id} \
  -H "X-API-Key: your-master-api-key" \
  -H "Content-Type: application/json" \
  -d '{"allow_self_registration": false}'
```

When disabled, `/api/auth/register` returns an error. Admin registration via `/api/admin/register` is unaffected.

### Authentication

#### Login

```bash
curl -X POST http://localhost:5678/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"site_id": 1, "email": "user@example.com", "password": "password"}'
```

Returns both `auth_token` (1 hour) and `refresh_token` (7 days).

#### Refresh Token

```bash
curl -X POST http://localhost:5678/api/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "..."}'
```

Returns new `auth_token` and rotated `refresh_token`. Token rotation detects theft by revoking all tokens if a used token is reused after the grace period.

### User Management

User management endpoints are scoped to sites.

#### Self-Registration (Public)

Allow users to register themselves. Password is optional - if omitted, users set their password during email verification.

**Note:** This endpoint can be disabled per site by setting `allow_self_registration: false` on the site. When disabled, returns `400 {"error": "Self-registration is not enabled for this site"}`.

```bash
# With password (traditional flow)
curl -X POST http://localhost:5678/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "site_id": 1,
    "email": "user@example.com",
    "password": "their-password"
  }'

# Without password (simpler UX - user sets password via email)
curl -X POST http://localhost:5678/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "site_id": 1,
    "email": "user@example.com"
  }'
```

Both flows send a verification email. The difference:
- **With password**: User clicks verification link → email verified → can login immediately
- **Without password**: User clicks verification link → sets password → email verified → can login

#### Admin User Registration

Create users via admin API. Users will set their own password during email verification (more secure than admin-set passwords).

```bash
curl -X POST http://localhost:5678/api/admin/register \
  -H "X-API-Key: your-master-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "site_id": 1,
    "email": "newuser@example.com",
    "role": "user"
  }'
```

The user will receive a verification email. When they click the link, they'll be prompted to set their password and complete account setup in one step.

**Note:** The `role` field is optional and defaults to `"user"`. Set to `"admin"` for admin users.

#### Check Verification Token

Check if a verification token is valid and whether password setup is required (used by frontend).

```bash
curl -X POST http://localhost:5678/api/auth/check-verification-token \
  -H "Content-Type: application/json" \
  -d '{"token": "verification-token-here"}'
```

Response:
```json
{
  "password_required": true,
  "email": "user@example.com"
}
```

#### Verify Email (with optional password)

Verify email and optionally set password for admin-created users.

```bash
# For admin-created users (password required)
curl -X POST http://localhost:5678/api/auth/verify-email \
  -H "Content-Type: application/json" \
  -d '{
    "token": "verification-token-here",
    "password": "user-chosen-password"
  }'

# For self-registered users (password optional, already set)
curl -X POST http://localhost:5678/api/auth/verify-email \
  -H "Content-Type: application/json" \
  -d '{"token": "verification-token-here"}'
```

## Admin Scripts

Convenient interactive scripts for common administrative tasks:

- **create-site.py** - Create a new tenant site
- **update-site.py** - Update an existing site's configuration
- **list-sites.py** - List all sites
- **create-user.py** - Create users (regular or admin) for existing sites
- **list-users.py** - List users for a site (by site ID or domain)
- **resend-verification.py** - Resend verification email for unverified users

All scripts use the master API key for authentication.

**Docker (production):**
```bash
docker exec -it aegis python admin_scripts/<script-name>.py
```

**Local development:**
```bash
source bin/activate && python admin_scripts/<script-name>.py
```

## Testing

Run the test suite:

```bash
source bin/activate && pytest
```

Run a specific test:

```bash
source bin/activate && pytest tests/test_specific_file.py::test_function_name
```

## Docker Deployment

ByteForge Aegis includes production-ready Docker configuration with Gunicorn, automated versioning, and container registry publishing.

### Prerequisites

- Docker Engine 20.10+
- Docker Compose 2.0+
- For publishing: authenticated to your container registry (GitHub Container Registry, Docker Hub, etc.)

### Quick Start with Docker Compose

1. **Create environment file**
   ```bash
   cp .env.docker.example .env
   # Edit .env with your configuration
   ```

2. **Generate security keys**
   ```bash
   # Generate SECRET_KEY
   openssl rand -hex 32

   # Generate MASTER_API_KEY
   openssl rand -hex 32
   ```

3. **Start services**
   ```bash
   docker-compose up -d
   ```

4. **Check health**
   ```bash
   curl http://localhost:5678/api/health
   ```

5. **View logs**
   ```bash
   docker-compose logs -f app
   ```

### Docker Compose Services

The `docker-compose.yml` defines two services:

- **db** - PostgreSQL 16 database (port 5432)
  - Container: `byteforge-aegis-db`
  - Database: `aegis`
  - User: `aegis_admin`
  - Includes health checks
  - Auto-loads schema on first run

- **app** - Flask application with Gunicorn (port 5678)
  - Container: `byteforge-aegis-app`
  - 4 Gunicorn workers for production
  - Non-root user for security
  - Health checks and auto-restart
  - Waits for database to be healthy

### Building and Publishing Images

The `build-publish.sh` script provides automated versioning and publishing to container registries.

**Standard build** (increments version, uses cache):
```bash
./build-publish.sh
```

**Fresh build** (no cache, latest dependencies):
```bash
./build-publish.sh --no-cache
```

**How versioning works:**
- First build creates `VERSION` file with version 1
- Each build increments version automatically
- Tags created: `{registry}:N` and `{registry}:latest`
- VERSION file is gitignored (build system manages it)

**Container Registry Setup:**

For GitHub Container Registry (ghcr.io):
```bash
# Set your GitHub Personal Access Token
export CR_PAT=ghp_your_token_here

# Login to GHCR
echo $CR_PAT | docker login ghcr.io -u your-username --password-stdin

# Build and publish
./build-publish.sh
```

For Docker Hub:
```bash
# Login
docker login docker.io

# Update registry URL in build-publish.sh
# Then build and publish
./build-publish.sh
```

### Production Deployment

**Using published images:**

```bash
docker pull ghcr.io/jmazzahacks/byteforge-aegis:latest

docker run -p 5678:5678 \
  -e SECRET_KEY=your-secret-key \
  -e MASTER_API_KEY=your-master-key \
  -e DB_HOST=your-db-host \
  -e DB_PASSWORD=your-db-password \
  -e MAILGUN_API_KEY=your-mailgun-key \
  -e MAILGUN_DOMAIN=your-domain.mailgun.org \
  ghcr.io/jmazzahacks/byteforge-aegis:latest
```

**With external PostgreSQL:**

Update your .env file or environment variables:
```bash
DB_HOST=your-postgres-host.example.com
DB_PORT=5432
DB_NAME=aegis
DB_USER=aegis_admin
DB_PASSWORD=your-secure-password
```

Then start only the app service:
```bash
docker-compose up app
```

### Docker Configuration Details

**Dockerfile features:**
- Python 3.11 slim base image
- Gunicorn with 4 workers (configurable)
- Non-root user (appuser) for security
- Health check endpoint monitoring
- PostgreSQL client tools included
- Admin scripts included for management

**Security:**
- Runs as non-root user
- Minimal base image
- No build secrets in final image
- Environment-based configuration

**Performance:**
- Layer caching for faster builds
- Gunicorn for concurrent requests
- Health checks for reliability
- Restart policies for availability

### Environment Variables (Docker)

Required variables for Docker deployment:

```bash
# Security (required)
SECRET_KEY=generate-with-openssl-rand-hex-32
MASTER_API_KEY=generate-with-openssl-rand-hex-32
DB_PASSWORD=your-database-password

# Mailgun (required for email)
MAILGUN_API_KEY=your-mailgun-api-key
MAILGUN_DOMAIN=your-domain.mailgun.org
EMAIL_FROM=noreply@yourdomain.com

# Aegis Frontend (required for email verification links)
AEGIS_FRONTEND_URL=https://aegis.yourdomain.com

# Optional (have defaults)
FLASK_ENV=production
FLASK_DEBUG=False
EMAIL_FROM_NAME=ByteForge Aegis
CORS_ORIGINS=*
AUTH_TOKEN_EXPIRATION=3600
EMAIL_VERIFICATION_EXPIRATION=86400
REFRESH_TOKEN_EXPIRATION=604800
REFRESH_TOKEN_ROTATION=true
REFRESH_TOKEN_GRACE_PERIOD=30
```

See `.env.docker.example` for complete configuration template.

### Troubleshooting Docker

**Container won't start:**
```bash
# Check logs
docker-compose logs app

# Check database connectivity
docker-compose exec app pg_isready -h db -U aegis_admin -d aegis
```

**Database connection issues:**
```bash
# Ensure database is healthy
docker-compose ps

# Check database logs
docker-compose logs db

# Restart services
docker-compose restart
```

**Reset database:**
```bash
# WARNING: This deletes all data!
docker-compose down -v
docker-compose up -d
```

**Build fails:**
```bash
# Clean rebuild
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### Docker Logs

Logs are configured with rotation:
- Max size: 10MB per file
- Max files: 3 files
- Format: JSON

View logs:
```bash
# All services
docker-compose logs -f

# App only
docker-compose logs -f app

# Database only
docker-compose logs -f db

# Last 100 lines
docker-compose logs --tail=100 app
```

## Related Projects

- **byteforge-aegis-client-js** - JavaScript/TypeScript API client
- **byteforge-aegis-frontend** - Next.js frontend application with centralized email verification

## Development Status

This project is under active development. APIs and implementation details may change.

## License

MIT

## Author

Jason Byteforge (@jmazzahacks)
