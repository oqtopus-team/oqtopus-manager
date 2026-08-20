"""Routes for the cloud-local .env file editor (view, lock, save, download)."""

from oqtopus_manager.routers._dotenv_routes import make_dotenv_router

router, api_router = make_dotenv_router(
    html_url_prefix="/cloud-local",
    api_url_prefix="/api/cloud-local",
    tags=["cloud-local"],
    release_diff_raw_url=(
        "https://raw.githubusercontent.com/oqtopus-team/oqtopus-cli"
        "/main/templates/cloud-local/config/.env"
    ),
    release_diff_display_url=(
        "https://github.com/oqtopus-team/oqtopus-cli"
        "/blob/main/templates/cloud-local/config/.env"
    ),
)
