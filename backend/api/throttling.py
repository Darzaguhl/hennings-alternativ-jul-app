from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class RegisterRateThrottle(AnonRateThrottle):
    """Public self-registration. Generous enough for a household signing up
    several people from one IP, tight enough to block scripted signups."""

    scope = "register"


class LoginRateThrottle(AnonRateThrottle):
    """Login attempts, keyed by IP -- slows down credential stuffing without
    locking out someone who just mistypes their password a few times."""

    scope = "login"


class PasswordSetupRequestRateThrottle(AnonRateThrottle):
    """The 'send me a password link' endpoint takes just an email and
    always responds the same way (see request_password_setup) -- without
    this, it's a free way to spam an arbitrary inbox or burn through the
    Resend quota."""

    scope = "password_setup_request"


class PasswordSetupConfirmRateThrottle(AnonRateThrottle):
    """Redeeming a password-setup/reset token. The token itself is
    unguessable (128 bits), so this is about limiting scripted noise
    rather than brute-forcing it."""

    scope = "password_setup_confirm"


class InventoryIdentifyRateThrottle(UserRateThrottle):
    """Each call spends a real Anthropic API request. Unlike the throttles
    above, this endpoint is already behind IsAuthenticated, so it's keyed
    per-user (UserRateThrottle) rather than per-IP -- several volunteers on
    the same venue wifi shouldn't share one limit."""

    scope = "inventory_identify"
