"""Routes for the backend service log viewer, stream, and download."""

from oqtopus_manager.routers._log_routes import make_log_router
from oqtopus_manager.routers.backend._utils import _get_log_file

router = make_log_router(
    url_prefix="/backend", tags=["backend"], get_log_file=_get_log_file
)
