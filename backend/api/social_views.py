"""Endpoints for "Sign in with Google / Microsoft" -- see api/social.py for
how a token is verified and api/models.SocialIdentity for what is stored.

The provider proves who someone is; what they may *do* still comes only from
our own memberships, so none of these endpoints grants or changes a role.

Linking an identity to an existing account is where this can go wrong, so
the rules are deliberate:
  * A returning identity (provider + subject) just logs in.
  * A first-time identity whose *verified* e-mail matches an account links
    to it automatically -- but only if that account has no password. The
    public signup lets anyone register any address, optionally with a
    password, without proving they own it and while handing out a session
    straight away; auto-linking onto such an account would let whoever
    pre-registered someone else's address keep their access after the real
    owner signs in. Accounts with a password therefore have to sign in with
    it once and link from inside (social_link), which proves both. And when
    we do link, every existing refresh token on the account is revoked, so a
    session handed out to a pre-registrant dies at that moment.
  * No account at all: a plain volunteer account is created (unless the
    client asked not to, as the admin dashboard does -- there signing in is
    pointless without an existing account).
"""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from .models import SocialIdentity
from .serializers import MeSerializer
from .social import SocialAuthError, configured_providers, verify_id_token
from .throttling import LoginRateThrottle

User = get_user_model()

PROFILE_FIELDS = ("first_name", "last_name", "phone", "address", "birthdate")


def _error(code, message, http_status):
    return Response({"code": code, "detail": message}, status=http_status)


def _profile_complete(user):
    return all(getattr(user, field) for field in PROFILE_FIELDS)


def _revoke_sessions(user):
    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)


def _login_response(request, user, created, http_status=status.HTTP_200_OK):
    refresh = RefreshToken.for_user(user)
    return Response(
        {
            "user": MeSerializer(user, context={"request": request}).data,
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "created": created,
            "profile_complete": _profile_complete(user),
        },
        status=http_status,
    )


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def social_providers(request):
    """Which providers are switched on, with the public web client IDs the
    website and admin dashboard need to start the provider's sign-in --
    so they need no configuration of their own."""
    return Response(configured_providers())


@api_view(["POST"])
@permission_classes([permissions.AllowAny])
@throttle_classes([LoginRateThrottle])
def social_login(request):
    """POST {provider, id_token, create?} -> JWT pair, like /api/token/.

    `create` (default true) lets a first-time sign-in with an unknown e-mail
    create a volunteer account; false answers 404 `no_account` instead."""

    try:
        claims = verify_id_token(request.data.get("provider"), request.data.get("id_token"))
    except SocialAuthError as err:
        return _error(err.code, err.message, err.status)

    identity = SocialIdentity.objects.select_related("user").filter(provider=claims.provider, subject=claims.subject).first()
    if identity:
        if not identity.user.is_active:
            return _error("inactive", "This account is disabled.", status.HTTP_403_FORBIDDEN)
        return _login_response(request, identity.user, created=False)

    if not claims.email or not claims.email_verified:
        return _error(
            "email_unverified",
            "The provider did not confirm your e-mail address, so we can't sign you in with it.",
            status.HTTP_400_BAD_REQUEST,
        )

    try:
        with transaction.atomic():
            user = User.objects.filter(email__iexact=claims.email).first()
            if user:
                if not user.is_active:
                    return _error("inactive", "This account is disabled.", status.HTTP_403_FORBIDDEN)
                if user.has_usable_password():
                    return _error(
                        "password_account",
                        "This e-mail already has an account with a password. Sign in with your password first, "
                        "then connect your Google/Microsoft account from there.",
                        status.HTTP_409_CONFLICT,
                    )
                _revoke_sessions(user)
                created = False
            else:
                if request.data.get("create", True) in (False, "false", "0", 0):
                    return _error(
                        "no_account", "There is no account for this e-mail address.", status.HTTP_404_NOT_FOUND
                    )
                user = User(
                    username=claims.email, email=claims.email, first_name=claims.first_name, last_name=claims.last_name
                )
                user.set_unusable_password()
                user.save()
                created = True
            SocialIdentity.objects.create(user=user, provider=claims.provider, subject=claims.subject, email=claims.email)
    except IntegrityError:
        # A concurrent first sign-in with the same identity/address won the
        # race; asking the client to retry lands on the "returning" path.
        return _error("conflict", "Please try signing in again.", status.HTTP_409_CONFLICT)

    return _login_response(request, user, created=created, http_status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


def _identities_payload(user):
    return [
        {"provider": i.provider, "email": i.email, "created_at": i.created_at}
        for i in user.social_identities.order_by("provider")
    ]


@api_view(["GET", "POST"])
@permission_classes([permissions.IsAuthenticated])
def social_identities(request):
    """GET: the providers linked to the signed-in user. POST {provider,
    id_token}: link another one. Linking needs no e-mail match -- being
    signed in here *and* at the provider is the proof -- which is also how
    an account with a password (see module docstring) gets connected."""

    if request.method == "GET":
        return Response(_identities_payload(request.user))

    try:
        claims = verify_id_token(request.data.get("provider"), request.data.get("id_token"))
    except SocialAuthError as err:
        return _error(err.code, err.message, err.status)

    existing = SocialIdentity.objects.filter(provider=claims.provider, subject=claims.subject).first()
    if existing:
        if existing.user_id == request.user.pk:
            return Response(_identities_payload(request.user))
        return _error(
            "already_linked", "That account is already connected to another user.", status.HTTP_409_CONFLICT
        )
    if request.user.social_identities.filter(provider=claims.provider).exists():
        return _error(
            "provider_already_linked", "You have already connected an account from that provider.", status.HTTP_409_CONFLICT
        )
    try:
        SocialIdentity.objects.create(
            user=request.user, provider=claims.provider, subject=claims.subject, email=claims.email
        )
    except IntegrityError:
        return _error("conflict", "Please try again.", status.HTTP_409_CONFLICT)
    return Response(_identities_payload(request.user), status=status.HTTP_201_CREATED)


@api_view(["DELETE"])
@permission_classes([permissions.IsAuthenticated])
def social_identity_detail(request, provider):
    """Unlink a provider -- refused if it's the only way the user can sign
    in (no password, no other provider), which would lock them out."""

    identity = request.user.social_identities.filter(provider=provider).first()
    if not identity:
        return _error("not_linked", "That provider is not connected.", status.HTTP_404_NOT_FOUND)
    if not request.user.has_usable_password() and request.user.social_identities.count() <= 1:
        return _error(
            "last_login_method",
            "This is your only way to sign in. Set a password first.",
            status.HTTP_400_BAD_REQUEST,
        )
    identity.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)
