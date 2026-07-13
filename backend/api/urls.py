from django.urls import include, path
from rest_framework import routers
from rest_framework_simplejwt.views import (TokenObtainPairView,
                                            TokenRefreshView)

from .views import (EventGroupInviteViewSet, EventInviteViewSet, EventViewSet,
                    GroupInviteViewSet, NotificationViewSet, QRCodeViewSet,
                    ShiftViewSet, SkillViewSet, UserGroupViewSet, UserViewSet)

router = routers.DefaultRouter()
router.register(r'users', UserViewSet)
router.register(r'events', EventViewSet)
router.register(r'shifts', ShiftViewSet)
router.register(r'skills', SkillViewSet)
router.register(r'qrcodes', QRCodeViewSet)
router.register(r'groups', UserGroupViewSet)
router.register(r'group-invites', GroupInviteViewSet, basename='group-invite')
router.register(r'event-invites', EventInviteViewSet, basename='event-invite')
router.register(r'event-group-invites', EventGroupInviteViewSet, basename='event-group-invite')
router.register(r'notifications', NotificationViewSet, basename='notification')

urlpatterns = [
    # JWT token endpoints
    path('token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # DRF router endpoints
    path('', include(router.urls)),  # this exposes /users/, /events/, /qrcodes/, /groups/
]
