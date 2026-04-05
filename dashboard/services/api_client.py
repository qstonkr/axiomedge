"""API client facade -- re-exports all functions for backward compatibility.

All 145+ public methods are split into domain sub-modules under services/api/.
This file re-exports everything so existing ``from services.api_client import X``
and ``from services import api_client; api_client.X`` continue to work.
"""

from services.api._core import *  # noqa: F401,F403
from services.api.kb import *  # noqa: F401,F403
from services.api.glossary import *  # noqa: F401,F403
from services.api.search import *  # noqa: F401,F403
from services.api.quality import *  # noqa: F401,F403
from services.api.admin import *  # noqa: F401,F403
from services.api.auth import *  # noqa: F401,F403
from services.api.misc import *  # noqa: F401,F403
