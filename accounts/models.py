from django.db import models
from django.contrib.auth.models import AbstractUser


class CustomUser(AbstractUser):
    ROLE_CHOICES = [
        ('doctor', 'Bác sĩ / Y tá'),
        ('admin', 'Quản trị viên'),
    ]
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='doctor')
    phone = models.CharField(max_length=20, blank=True, default='')
    department = models.CharField(max_length=100, blank=True, default='')

    class Meta:
        db_table = 'users'

    def is_admin_user(self):
        return self.role == 'admin'

    def is_doctor(self):
        return self.role == 'doctor'
