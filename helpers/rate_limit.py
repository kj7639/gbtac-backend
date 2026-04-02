"""
rate_limit.py

Configures the application-wide rate limiter using SlowAPI. Uses the client's
remote IP address as the key for rate limiting. Imported and attached to the
FastAPI app in main.py.

Author: Anna Yabut
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)