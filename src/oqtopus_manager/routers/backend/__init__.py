"""Backend router package — collects all backend APIRouters."""

from oqtopus_manager.routers.backend.detail import router as detail_router
from oqtopus_manager.routers.backend.dotenv import router as dotenv_router
from oqtopus_manager.routers.backend.list import router as list_router
from oqtopus_manager.routers.backend.log import router as log_router
from oqtopus_manager.routers.backend.service_config import (
    router as service_config_router,
)

routers = [list_router, detail_router, dotenv_router, service_config_router, log_router]  # noqa: RUF067
