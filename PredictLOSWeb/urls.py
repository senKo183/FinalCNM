from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('patients/', include('patients.urls')),
    path('ml/', include('ml_engine.urls')),
    path('dashboard/', include('dashboard.urls')),
    path('', lambda request: redirect('dashboard:index')),
]
