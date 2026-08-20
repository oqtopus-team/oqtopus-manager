"""Cloud-local router package — collects all cloud-local APIRouters."""

from oqtopus_manager.routers.cloud_local.detail import api_router as detail_api_router
from oqtopus_manager.routers.cloud_local.detail import router as detail_router
from oqtopus_manager.routers.cloud_local.dotenv import api_router as dotenv_api_router
from oqtopus_manager.routers.cloud_local.dotenv import router as dotenv_router
from oqtopus_manager.routers.cloud_local.list import api_router as list_api_router
from oqtopus_manager.routers.cloud_local.list import router as list_router
from oqtopus_manager.routers.cloud_local.log import api_router as log_api_router
from oqtopus_manager.routers.cloud_local.log import router as log_router

# HTML pages, kept at /cloud-local
routers = [list_router, detail_router, dotenv_router, log_router]  # noqa: RUF067
# JSON/Server-Sent Events/download endpoints, under /api/cloud-local
api_routers = [  # noqa: RUF067
    list_api_router,
    detail_api_router,
    dotenv_api_router,
    log_api_router,
]
