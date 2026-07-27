"""
Tests for per-user deletion protection.

Covers the delete refusal, the distinct error code, the absence of a
user.deleted webhook on refusal, and the admin PATCH that sets the flag.
"""
from config import get_config
from database import db_manager
from services.webhook_service import webhook_service


def _master_headers() -> dict:
    return {'X-API-Key': get_config().MASTER_API_KEY}


def test_protected_user_delete_refused(test_client, sample_site, sample_user, admin_user):
    """A deletion-protected user is not deletable, and survives the attempt."""
    sample_user.deletion_protected = True
    db_manager.update_user(sample_user)

    response = test_client.delete(
        f'/api/admin/users/{sample_user.uuid}',
        headers=_master_headers()
    )

    assert response.status_code == 409
    assert response.get_json()['code'] == 'user_deletion_protected'
    assert db_manager.find_user_by_uuid(sample_user.uuid) is not None


def test_protected_delete_fires_no_webhook(test_client, sample_site, sample_user, admin_user, monkeypatch):
    """A refused delete is not a deletion — no user.deleted event."""
    sent = []

    def capture_webhook(site, payload) -> None:
        sent.append(payload)

    monkeypatch.setattr(webhook_service, 'send_webhook', capture_webhook)

    sample_user.deletion_protected = True
    db_manager.update_user(sample_user)

    response = test_client.delete(
        f'/api/admin/users/{sample_user.uuid}',
        headers=_master_headers()
    )

    assert response.status_code == 409
    assert sent == []


def test_last_admin_refusal_has_distinct_code(test_client, sample_site, admin_user):
    """The two 409 refusals are distinguishable by code."""
    response = test_client.delete(
        f'/api/admin/users/{admin_user.uuid}',
        headers=_master_headers()
    )

    assert response.status_code == 409
    assert response.get_json()['code'] == 'last_site_admin'


def test_unprotected_user_still_deletable(test_client, sample_site, sample_user, admin_user):
    """Default is unprotected — existing delete behavior is unchanged."""
    assert sample_user.deletion_protected is False

    response = test_client.delete(
        f'/api/admin/users/{sample_user.uuid}',
        headers=_master_headers()
    )

    assert response.status_code == 200
    assert db_manager.find_user_by_uuid(sample_user.uuid) is None


def test_patch_sets_and_clears_protection(test_client, sample_site, sample_user):
    """The admin PATCH toggles the flag, and clearing it re-enables deletion."""
    response = test_client.patch(
        f'/api/admin/users/{sample_user.uuid}',
        json={'deletion_protected': True},
        headers=_master_headers()
    )
    assert response.status_code == 200
    assert response.get_json()['deletion_protected'] is True
    assert db_manager.find_user_by_uuid(sample_user.uuid).deletion_protected is True

    response = test_client.patch(
        f'/api/admin/users/{sample_user.uuid}',
        json={'deletion_protected': False},
        headers=_master_headers()
    )
    assert response.status_code == 200
    assert response.get_json()['deletion_protected'] is False

    # Unprotect-then-delete is the intended clearing path.
    response = test_client.delete(
        f'/api/admin/users/{sample_user.uuid}',
        headers=_master_headers()
    )
    assert response.status_code == 200


def test_user_protection_cannot_be_cleared_with_a_string(test_client, sample_site, sample_user):
    """Only real JSON booleans clear per-user protection, same as site-level."""
    sample_user.deletion_protected = True
    db_manager.update_user(sample_user)

    for falsy in ['false', '0', 'off', 'no', 0, 0.0, 1, 'true', None]:
        response = test_client.patch(
            f'/api/admin/users/{sample_user.uuid}',
            json={'deletion_protected': falsy},
            headers=_master_headers()
        )
        assert response.status_code == 400, f'{falsy!r} was accepted'
        assert db_manager.find_user_by_uuid(sample_user.uuid).deletion_protected is True


def test_patch_requires_master_key(test_client, sample_site, sample_user):
    """Without the master key the flag cannot be changed."""
    response = test_client.patch(
        f'/api/admin/users/{sample_user.uuid}',
        json={'deletion_protected': True}
    )

    assert response.status_code == 401
    assert db_manager.find_user_by_uuid(sample_user.uuid).deletion_protected is False


def test_patch_unknown_user_returns_404(test_client, clean_database):
    response = test_client.patch(
        '/api/admin/users/0191e1a0-0000-7000-8000-0000000000ff',
        json={'deletion_protected': True},
        headers=_master_headers()
    )

    assert response.status_code == 404


def test_site_protection_blocks_deleting_any_user(test_client, sample_site, sample_user, admin_user):
    """A protected site makes every one of its users undeletable, with no
    per-user marking required — this is the tenant-wide guarantee."""
    sample_site.deletion_protected = True
    db_manager.update_site(sample_site)

    assert sample_user.deletion_protected is False

    response = test_client.delete(
        f'/api/admin/users/{sample_user.uuid}',
        headers=_master_headers()
    )

    assert response.status_code == 409
    assert response.get_json()['code'] == 'site_deletion_protected'
    assert db_manager.find_user_by_uuid(sample_user.uuid) is not None


def test_site_protection_fires_no_webhook(test_client, sample_site, sample_user, admin_user, monkeypatch):
    """A refused delete under site protection emits no user.deleted event."""
    sent = []

    def capture_webhook(site, payload) -> None:
        sent.append(payload)

    monkeypatch.setattr(webhook_service, 'send_webhook', capture_webhook)

    sample_site.deletion_protected = True
    db_manager.update_site(sample_site)

    response = test_client.delete(
        f'/api/admin/users/{sample_user.uuid}',
        headers=_master_headers()
    )

    assert response.status_code == 409
    assert sent == []


def test_protected_site_cannot_be_deleted(test_client, sample_site, sample_user):
    """The site itself is undeletable while protected."""
    sample_site.deletion_protected = True
    db_manager.update_site(sample_site)

    response = test_client.delete(
        f'/api/sites/{sample_site.uuid}',
        headers=_master_headers()
    )

    assert response.status_code == 409
    assert response.get_json()['code'] == 'site_deletion_protected'
    assert db_manager.find_site_by_uuid(sample_site.uuid) is not None


def test_list_sites_reports_protection(test_client, sample_site):
    """GET /api/sites must reflect real protection state — a list that always
    says 'unprotected' would tell an operator it is safe to prune."""
    sample_site.deletion_protected = True
    db_manager.update_site(sample_site)

    response = test_client.get('/api/sites', headers=_master_headers())

    assert response.status_code == 200
    listed = [s for s in response.get_json() if s['uuid'] == sample_site.uuid]
    assert len(listed) == 1
    assert listed[0]['deletion_protected'] is True


def test_protection_cannot_be_cleared_with_a_string(test_client, sample_site):
    """Only real JSON booleans are accepted: "false" and friends must not
    coerce to False and silently clear protection."""
    sample_site.deletion_protected = True
    db_manager.update_site(sample_site)

    for falsy in ['false', '0', 'off', 'no', 0, 0.0, 1, 'true', None]:
        response = test_client.put(
            f'/api/sites/{sample_site.uuid}',
            json={'deletion_protected': falsy},
            headers=_master_headers()
        )
        assert response.status_code == 400, f'{falsy!r} was accepted'
        assert db_manager.find_site_by_uuid(sample_site.uuid).deletion_protected is True


def test_site_protection_settable_via_update_site(test_client, sample_site):
    """The flag is set through the existing admin update-site endpoint."""
    response = test_client.put(
        f'/api/sites/{sample_site.uuid}',
        json={'deletion_protected': True},
        headers=_master_headers()
    )

    assert response.status_code == 200
    assert response.get_json()['deletion_protected'] is True
    assert db_manager.find_site_by_uuid(sample_site.uuid).deletion_protected is True


def test_unprotected_site_users_still_deletable(test_client, sample_site, sample_user, admin_user):
    """Default is unprotected — existing behavior is unchanged."""
    assert sample_site.deletion_protected is False

    response = test_client.delete(
        f'/api/admin/users/{sample_user.uuid}',
        headers=_master_headers()
    )

    assert response.status_code == 200


def test_site_delete_refused_when_it_has_protected_users(test_client, sample_site, sample_user):
    """Site deletion cascades to users, so it must respect their protection."""
    sample_user.deletion_protected = True
    db_manager.update_user(sample_user)

    response = test_client.delete(
        f'/api/sites/{sample_site.uuid}',
        headers=_master_headers()
    )

    assert response.status_code == 409
    assert response.get_json()['code'] == 'site_has_protected_users'
    assert db_manager.find_site_by_uuid(sample_site.uuid) is not None
    assert db_manager.find_user_by_uuid(sample_user.uuid) is not None


def test_site_delete_allowed_without_protected_users(test_client, sample_site, sample_user):
    """Sites with no protected users delete as before."""
    response = test_client.delete(
        f'/api/sites/{sample_site.uuid}',
        headers=_master_headers()
    )

    assert response.status_code == 200
    assert db_manager.find_site_by_uuid(sample_site.uuid) is None


def test_user_response_exposes_flag(test_client, sample_site, sample_user, admin_user):
    """Consumers can tell a protected account from an unprotected one."""
    sample_user.deletion_protected = True
    db_manager.update_user(sample_user)

    response = test_client.get(
        f'/api/sites/{sample_site.uuid}/users/{sample_user.uuid}',
        headers={'X-Tenant-Api-Key': sample_site.tenant_api_key}
    )

    assert response.status_code == 200
    assert response.get_json()['deletion_protected'] is True
