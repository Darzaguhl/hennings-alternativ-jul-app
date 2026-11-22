"""Verifying "Sign in with Google / Microsoft" ID tokens.

The clients (website, admin dashboard, mobile app) run the provider's own
sign-in and hand us the resulting OpenID Connect ID token; we never see a
password. Here we check the token the way the provider documents: signature
against the provider's published keys, issuer, audience (it must have been
minted for *our* client ID, not some other app's), and expiry.

What we trust from a verified token is *only* what the provider vouches
for. The one subtle part is e-mail: we link and create accounts by e-mail,
so we need the provider to have verified it.
  * Google sets `email_verified`.
  * Microsoft doesn't, and the `email` claim of a work/school account is
    not guaranteed to be controlled by that person ("nOAuth": a tenant admin
    can set any address). We therefore treat the e-mail as verified only for
    personal Microsoft accounts (the "consumers" tenant, where the address is
    the account itself) or when the tenant has asserted domain ownership via
    the optional `xms_edov` claim. Anything else signs in as "unverified" and
    is refused -- better to reject a legitimate work account than to let
    someone claim another person's address.
"""

from dataclasses import dataclass

import jwt
from django.conf import settings

PROVIDER_GOOGLE = "google"
PROVIDER_MICROSOFT = "microsoft"
PROVIDERS = (PROVIDER_GOOGLE, PROVIDER_MICROSOFT)

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")

MICROSOFT_JWKS_URL = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
# Tenant ID of personal Microsoft accounts (outlook.com, hotmail, live...).
MICROSOFT_CONSUMERS_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"


class SocialAuthError(Exception):
    """A sign-in we refuse. `code` is stable for clients to branch on."""

    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class SocialClaims:
    provider: str
    subject: str
    email: str
    email_verified: bool
    first_name: str
    last_name: str


_jwk_clients = {}


def _jwk_client(url):
    # PyJWKClient caches the fetched key set, so this isn't a network call
    # per login.
    if url not in _jwk_clients:
        _jwk_clients[url] = jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)
    return _jwk_clients[url]


def google_client_ids():
    ids = [settings.GOOGLE_OAUTH_WEB_CLIENT_ID, *settings.GOOGLE_OAUTH_EXTRA_CLIENT_IDS]
    return [i for i in ids if i]


def configured_providers():
    """What the clients may offer, plus the *public* web client IDs they
    need to start the provider's sign-in (client IDs are not secrets)."""
    result = {}
    if settings.GOOGLE_OAUTH_WEB_CLIENT_ID:
        result[PROVIDER_GOOGLE] = {"client_id": settings.GOOGLE_OAUTH_WEB_CLIENT_ID}
    if settings.MICROSOFT_OAUTH_CLIENT_ID:
        result[PROVIDER_MICROSOFT] = {"client_id": settings.MICROSOFT_OAUTH_CLIENT_ID}
    return result


def _decode(token, jwks_url, **kwargs):
    try:
        key = _jwk_client(jwks_url).get_signing_key_from_jwt(token).key
        return jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            **kwargs,
        )
    except jwt.PyJWTError:
        raise SocialAuthError("invalid_token", "The sign-in could not be verified. Try again.")


def _truthy(value):
    return value is True or str(value).lower() == "true"


def _split_name(full_name):
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


def verify_google(token):
    audiences = google_client_ids()
    if not audiences:
        raise SocialAuthError("provider_not_configured", "Google sign-in is not set up.", status=503)
    payload = _decode(token, GOOGLE_JWKS_URL, audience=audiences, issuer=list(GOOGLE_ISSUERS))
    return SocialClaims(
        provider=PROVIDER_GOOGLE,
        subject=str(payload["sub"]),
        email=(payload.get("email") or "").strip().lower(),
        email_verified=_truthy(payload.get("email_verified")),
        first_name=payload.get("given_name") or "",
        last_name=payload.get("family_name") or "",
    )


def verify_microsoft(token):
    audience = settings.MICROSOFT_OAUTH_CLIENT_ID
    if not audience:
        raise SocialAuthError("provider_not_configured", "Microsoft sign-in is not set up.", status=503)
    payload = _decode(token, MICROSOFT_JWKS_URL, audience=audience)

    # The issuer embeds the tenant, so it can't be a fixed string for a
    # multi-tenant app -- check it against the (now signature-verified)
    # tenant claim instead.
    tenant = payload.get("tid")
    if not tenant or payload["iss"] != f"https://login.microsoftonline.com/{tenant}/v2.0":
        raise SocialAuthError("invalid_token", "The sign-in could not be verified. Try again.")

    personal = tenant == MICROSOFT_CONSUMERS_TENANT
    email = (payload.get("email") or "").strip().lower()
    if not email and personal:
        # Personal-account tokens often carry the address only as the
        # username; for those it is the account itself.
        email = (payload.get("preferred_username") or "").strip().lower()
    first, last = payload.get("given_name") or "", payload.get("family_name") or ""
    if not (first or last):
        first, last = _split_name(payload.get("name"))
    return SocialClaims(
        provider=PROVIDER_MICROSOFT,
        # `sub` is only unique per app registration; `oid` is the stable
        # per-account id across apps, but we only ever see our own app, so
        # `sub` is the right, stable key for us.
        subject=str(payload["sub"]),
        email=email if "@" in email else "",
        email_verified=bool(email) and (personal or _truthy(payload.get("xms_edov"))),
        first_name=first,
        last_name=last,
    )


def verify_id_token(provider, token):
    """Verified claims for a provider's ID token, or SocialAuthError."""
    if provider not in PROVIDERS:
        raise SocialAuthError("unknown_provider", "Unknown sign-in provider.")
    if not token or not isinstance(token, str):
        raise SocialAuthError("invalid_token", "The sign-in could not be verified. Try again.")
    return verify_google(token) if provider == PROVIDER_GOOGLE else verify_microsoft(token)
