from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

User = get_user_model()


class EmailBackend(ModelBackend):
    """Authenticate by email+password instead of username+password.

    Kept alongside the default ModelBackend (see AUTHENTICATION_BACKENDS)
    so the Django admin login, which posts "username", still works.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        email = kwargs.get("email") or username
        if not email or not password:
            return None
        # .first() rather than .get(): email has a DB-level case-insensitive
        # unique constraint (see User.Meta), but staying defensive here
        # means a duplicate slipping through some other path (a pre-
        # constraint row, a direct DB write) degrades to "picks one" rather
        # than an uncaught MultipleObjectsReturned 500 on every login
        # attempt for that address.
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
