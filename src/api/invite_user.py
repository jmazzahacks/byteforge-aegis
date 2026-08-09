"""
Tenant-key endpoint for inviting a user onto the caller's own site.

This exists because the two obvious alternatives are each wrong for an
integrating backend that wants to invite someone:

  /api/auth/register     is the public signup path. It refuses unless the
                         site has allow_self_registration enabled, so an
                         invite-only site had to switch ON a flag named
                         "allow self registration" in order to send
                         invitations — backwards on its face, and it left
                         the console misreporting how the site behaves.
                         It is also enumeration-safe by design, returning
                         an identical 201 whether it created a user or
                         silently did nothing, so the caller cannot tell
                         whether the invite actually landed.

  /api/admin/register    does the right thing but is master-key gated, and
                         the master key spans every site. Reaching one
                         site's user list should not require a credential
                         that reaches all of them.

The authorization argument for using the tenant key here: the decision
that *this person may invite that person* belongs to the tenant, who made
it against their own session and their own rules before calling. Aegis is
only being told "a holder of this site's key asked for a user on this
site" — which is exactly what the tenant key attests, and it cannot
attest it for any other site.
"""
from flask import Blueprint, g, jsonify

from schemas.auth_schemas import InviteUserSchema, UserResponseSchema
from services.auth_service import auth_service
from utils.tenant_api_key_middleware import require_tenant_api_key
from utils.validators import validate_request

invite_user_bp = Blueprint('invite_user', __name__)


@invite_user_bp.route('/api/auth/invite-user', methods=['POST'])
@require_tenant_api_key
@validate_request(InviteUserSchema)
def invite_user(validated_data: dict):
    """
    Invite a user onto the site the supplied tenant API key belongs to.

    Requires the site's tenant API key (X-Tenant-Api-Key header).

    The user is created without a password and is sent a verification
    email carrying a link to the site's own frontend. They set their
    password when they follow it, at which point `user.verified` fires.
    Nothing is dispatched at invite time — a user who never accepts
    produces no webhook at all, so callers must expire their own pending
    invitations rather than wait for a signal that is not coming.

    Request body:
        site_id: The caller's own site. Used by the tenant-key middleware
            to pick which key to authenticate against; the user is created
            on the authenticated site regardless.
        email: Address to invite.

    Re-inviting someone who has not accepted yet resends the link and
    returns them, rather than failing as a duplicate — a verification token
    expires in 24 hours but the user row does not, so without this an
    ignored or spam-filtered invitation would lock the invitee out
    permanently. Only established accounts are refused.

    Returns:
        201: User invited and verification email sent (new or resent)
        400: Validation error, or the address belongs to an established
             account on this site
        401: Missing or invalid tenant API key
    """
    # g.tenant_site is the site the middleware actually authenticated, not
    # a re-resolution of the request body. Resolving the identifier a
    # second time is how a handler ends up authorizing against a different
    # site than the one whose key was checked.
    site = g.tenant_site

    try:
        # invite_user, not register_user: it bypasses the
        # allow_self_registration gate, always assigns UserRole.USER, makes
        # an established duplicate a real 400 instead of a silent no-op, and
        # re-sends when the address is a still-pending invitation.
        user = auth_service.invite_user(
            site_uuid=site.uuid,
            email=validated_data['email'],
        )
        return jsonify(UserResponseSchema().dump(user)), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
