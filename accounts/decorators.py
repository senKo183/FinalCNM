from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages


def role_required(role):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')
            if request.user.role != role and not request.user.is_superuser:
                messages.error(request, 'Bạn không có quyền truy cập trang này.')
                return redirect('dashboard:index')
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def admin_required(view_func):
    return role_required('admin')(view_func)
