"""ASGI entrypoint."""

import os

from .api import app_from_environment
from .observability import configure_logging

configure_logging(os.getenv("LOG_LEVEL", "INFO"))

app = app_from_environment()
