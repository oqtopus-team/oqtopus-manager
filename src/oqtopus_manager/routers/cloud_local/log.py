"""Routes for the cloud-local service log viewer, stream, and download."""

from oqtopus_manager.routers._log_routes import make_log_router
from oqtopus_manager.services.cloud_local import get_log_file

router, api_router = make_log_router(
    html_url_prefix="/cloud-local",
    api_url_prefix="/api/cloud-local",
    tags=["cloud-local"],
    get_log_file=get_log_file,
)
