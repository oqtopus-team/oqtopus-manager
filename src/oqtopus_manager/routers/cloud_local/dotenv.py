"""Routes for the cloud-local .env file editor (view, lock, save, download)."""

from oqtopus_manager.routers._dotenv_routes import make_dotenv_router

router = make_dotenv_router(
    url_prefix="/cloud-local",
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
