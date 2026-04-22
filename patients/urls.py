from django.urls import path
from . import views

app_name = 'patients'

urlpatterns = [
    path('', views.patient_list, name='list'),
    path('create/', views.patient_create, name='create'),
    path('api/lookup-cccd/', views.lookup_cccd, name='lookup_cccd'),
    path('<str:patient_id>/', views.patient_detail, name='detail'),
    path('<str:patient_id>/edit/', views.patient_edit, name='edit'),
    path('<str:patient_id>/discharge/', views.patient_discharge, name='discharge'),
]
