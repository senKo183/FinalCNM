from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from rest_framework_simplejwt.tokens import RefreshToken
from .models import CustomUser
from .decorators import admin_required


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard:index')

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            refresh = RefreshToken.for_user(user)
            request.session['jwt_access'] = str(refresh.access_token)
            request.session['jwt_refresh'] = str(refresh)
            next_url = request.GET.get('next', 'dashboard:index')
            return redirect(next_url)
        else:
            messages.error(request, 'Tên đăng nhập hoặc mật khẩu không đúng.')

    return render(request, 'accounts/login.html')


def logout_view(request):
    logout(request)
    request.session.flush()
    messages.success(request, 'Đã đăng xuất thành công.')
    return redirect('accounts:login')


@login_required
def profile_view(request):
    if request.method == 'POST':
        user = request.user
        user.first_name = request.POST.get('first_name', '')
        user.last_name = request.POST.get('last_name', '')
        user.email = request.POST.get('email', '')
        user.phone = request.POST.get('phone', '')
        user.department = request.POST.get('department', '')
        user.save()
        messages.success(request, 'Cập nhật thông tin thành công.')
        return redirect('accounts:profile')

    return render(request, 'accounts/profile.html')


@login_required
def change_password_view(request):
    if request.method == 'POST':
        old_password = request.POST.get('old_password')
        new_password = request.POST.get('new_password')
        confirm_password = request.POST.get('confirm_password')

        if not request.user.check_password(old_password):
            messages.error(request, 'Mật khẩu hiện tại không đúng.')
        elif new_password != confirm_password:
            messages.error(request, 'Mật khẩu mới không khớp.')
        elif len(new_password) < 8:
            messages.error(request, 'Mật khẩu phải có ít nhất 8 ký tự.')
        else:
            request.user.set_password(new_password)
            request.user.save()
            update_session_auth_hash(request, request.user)
            messages.success(request, 'Đổi mật khẩu thành công.')
            return redirect('accounts:profile')

    return render(request, 'accounts/change_password.html')


@login_required
@admin_required
def manage_users_view(request):
    users = CustomUser.objects.all().order_by('-date_joined')
    return render(request, 'accounts/manage_users.html', {'users': users})


@login_required
@admin_required
def create_user_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        email = request.POST.get('email', '')
        first_name = request.POST.get('first_name', '')
        last_name = request.POST.get('last_name', '')
        role = request.POST.get('role', 'doctor')
        phone = request.POST.get('phone', '')
        department = request.POST.get('department', '')

        if CustomUser.objects.filter(username=username).exists():
            messages.error(request, f'Tên đăng nhập "{username}" đã tồn tại.')
        else:
            user = CustomUser.objects.create_user(
                username=username, password=password, email=email,
                first_name=first_name, last_name=last_name,
                role=role, phone=phone, department=department,
            )
            messages.success(request, f'Tạo tài khoản "{username}" thành công.')
            return redirect('accounts:manage_users')

    return render(request, 'accounts/create_user.html')


@login_required
@admin_required
def toggle_user_active(request, user_id):
    user = get_object_or_404(CustomUser, pk=user_id)
    if user == request.user:
        messages.error(request, 'Không thể vô hiệu hóa tài khoản của chính bạn.')
    else:
        user.is_active = not user.is_active
        user.save()
        status = 'kích hoạt' if user.is_active else 'vô hiệu hóa'
        messages.success(request, f'Đã {status} tài khoản "{user.username}".')
    return redirect('accounts:manage_users')
