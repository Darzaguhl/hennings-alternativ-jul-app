from django.urls import include, path
from rest_framework import routers
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (EmailTokenObtainPairView, EventViewSet, QRCodeViewSet,
                    RegisterView, ShiftViewSet, SkillViewSet, UserViewSet,
                    accept_invite, invite_preview, public_event, public_skills)

router = routers.DefaultRouter()
router.register(r'users', UserViewSet)
router.register(r'events', EventViewSet)
router.register(r'shifts', ShiftViewSet)
router.register(r'skills', SkillViewSet)
router.register(r'qrcodes', QRCodeViewSet)

urlpatterns = [
    # Public registration
    path('register/', RegisterView.as_view(), name='register'),

    # Public read-only: the current event + oppgaver, for the website
    # signup page. See public_event's docstring for why this isn't just
    # EventViewSet/ShiftViewSet opened up to AllowAny.
    path('public/event/', public_event, name='public-event'),
    path('public/skills/', public_skills, name='public-skills'),

    # Public: admin/staff invite accept flow
    path('invites/accept/', accept_invite, name='accept-invite'),
    path('invites/<str:token>/', invite_preview, name='invite-preview'),

    # JWT token endpoints (login by email)
    path('token/', EmailTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # DRF router endpoints
    path('', include(router.urls)),  # this exposes /users/, /events/, /shifts/, /skills/, /qrcodes/
]
