import os
from pathlib import Path
from datetime import timedelta
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY')
DEBUG = config('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'accounts',
    'patients',
    'ml_engine',
    'dashboard',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'PredictLOSWeb.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'PredictLOSWeb.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'vi'
TIME_ZONE = 'Asia/Ho_Chi_Minh'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# MongoDB
MONGODB_URI = config('MONGODB_URI')
MONGODB_NAME = config('MONGODB_NAME', default='predictlos_db')

# REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
}

# JWT
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=12),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
}

# Celery / Redis
CELERY_BROKER_URL = config('CELERY_BROKER_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = config('CELERY_RESULT_BACKEND', default='redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
REDIS_URL = config('REDIS_URL', default='redis://localhost:6379/0')

# ML Config
ML_MODELS_DIR = BASE_DIR / 'ml_models'
CSV_DATA_PATH = BASE_DIR / 'LengthOfStay.csv'
RETRAIN_SAMPLE_THRESHOLD = config('RETRAIN_SAMPLE_THRESHOLD', default=10, cast=int)
RETRAIN_DAYS_THRESHOLD = config('RETRAIN_DAYS_THRESHOLD', default=7, cast=int)

# MLflow (v2 — Giai đoạn 1 Foundation)
MLFLOW_TRACKING_URI = config('MLFLOW_TRACKING_URI', default='http://localhost:5000')
MLFLOW_S3_ENDPOINT_URL = config('MLFLOW_S3_ENDPOINT_URL', default='http://localhost:9000')
MLFLOW_EXPERIMENT_NAME = config('MLFLOW_EXPERIMENT_NAME', default='los_prediction')
MLFLOW_REGISTERED_MODEL_NAME = config('MLFLOW_REGISTERED_MODEL_NAME', default='los_model')
MLFLOW_AWS_ACCESS_KEY_ID = config('AWS_ACCESS_KEY_ID', default='minio_admin')
MLFLOW_AWS_SECRET_ACCESS_KEY = config('AWS_SECRET_ACCESS_KEY', default='minio_password')

# Evidently + SHAP (v2 — Giai đoạn 2 Monitoring & Explainability)
REFERENCE_DATA_PATH = BASE_DIR / 'reference_data.csv'
EVIDENTLY_DRIFT_THRESHOLD_HIGH = config('EVIDENTLY_DRIFT_THRESHOLD_HIGH', default=0.5, cast=float)
EVIDENTLY_DRIFT_THRESHOLD_MEDIUM = config('EVIDENTLY_DRIFT_THRESHOLD_MEDIUM', default=0.25, cast=float)

# Make MLflow S3 creds available to boto3 used by mlflow artifact upload
os.environ.setdefault('MLFLOW_S3_ENDPOINT_URL', MLFLOW_S3_ENDPOINT_URL)
os.environ.setdefault('AWS_ACCESS_KEY_ID', MLFLOW_AWS_ACCESS_KEY_ID)
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', MLFLOW_AWS_SECRET_ACCESS_KEY)

# Login redirect
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/dashboard/'

AUTH_USER_MODEL = 'accounts.CustomUser'
