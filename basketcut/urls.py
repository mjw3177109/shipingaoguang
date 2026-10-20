from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from highlights import health

urlpatterns = [
    path("health/live", health.health_live, name="health_live"),
    path("health/ready", health.health_ready, name="health_ready"),
    path("health/worker", health.health_worker, name="health_worker"),
    path("admin/", admin.site.urls),
    path("accounts/login/", auth_views.LoginView.as_view(template_name="login.html"), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("highlights.urls")),
]
