"""Cloud-local router package — collects all cloud-local APIRouters."""

from oqtopus_manager.routers.cloud_local.detail import router as detail_router
from oqtopus_manager.routers.cloud_local.dotenv import router as dotenv_router
from oqtopus_manager.routers.cloud_local.list import router as list_router
from oqtopus_manager.routers.cloud_local.log import router as log_router

routers = [list_router, detail_router, dotenv_router, log_router]  # noqa: RUF067
