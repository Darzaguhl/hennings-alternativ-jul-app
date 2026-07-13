from django.urls import include, path
from rest_framework import routers
from rest_framework_simplejwt.views import (TokenObtainPairView,
                                            TokenRefreshView)

from .views import (EventViewSet, QRCodeViewSet, ShiftViewSet,
                    SkillViewSet, UserViewSet)

router = routers.DefaultRouter()
router.register(r'users', UserViewSet)
router.register(r'events', EventViewSet)
router.register(r'shifts', ShiftViewSet)
router.register(r'skills', SkillViewSet)
router.register(r'qrcodes', QRCodeViewSet)

urlpatterns = [
    # JWT token endpoints
    path('token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # DRF router endpoints
    path('', include(router.urls)),  # this exposes /users/, /events/, /shifts/, /skills/, /qrcodes/
]
