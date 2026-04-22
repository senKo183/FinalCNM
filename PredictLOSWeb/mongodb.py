from pymongo import MongoClient
from django.conf import settings

_client = None
_db = None


def get_db():
    global _client, _db
    if _db is None:
        _client = MongoClient(settings.MONGODB_URI)
        _db = _client[settings.MONGODB_NAME]
    return _db


def get_collection(name):
    return get_db()[name]
