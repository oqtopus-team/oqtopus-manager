"""Backend router package — collects all backend APIRouters."""

from oqtopus_manager.routers.backend.detail import api_router as detail_api_router
from oqtopus_manager.routers.backend.detail import router as detail_router
from oqtopus_manager.routers.backend.dotenv import api_router as dotenv_api_router
from oqtopus_manager.routers.backend.dotenv import router as dotenv_router
from oqtopus_manager.routers.backend.list import api_router as list_api_router
from oqtopus_manager.routers.backend.list import router as list_router
from oqtopus_manager.routers.backend.log import api_router as log_api_router
from oqtopus_manager.routers.backend.log import router as log_router
from oqtopus_manager.routers.backend.service_config import (
    api_router as service_config_api_router,
)
from oqtopus_manager.routers.backend.service_config import (
    router as service_config_router,
)

# HTML pages, kept at /backend
routers = [list_router, detail_router, dotenv_router, service_config_router, log_router]  # noqa: RUF067
# JSON/Server-Sent Events/download endpoints, under /api/backend
api_routers = [  # noqa: RUF067
    list_api_router,
    detail_api_router,
    dotenv_api_router,
    service_config_api_router,
    log_api_router,
]
