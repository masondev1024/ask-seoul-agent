"""ASK Seoul evidence-first agent service."""

import logging

# Library imports must not fall through to logging.lastResort. The ASGI
# entrypoint installs the production JSON handler explicitly.
logging.getLogger(__name__).addHandler(logging.NullHandler())

__version__ = "0.1.0"
