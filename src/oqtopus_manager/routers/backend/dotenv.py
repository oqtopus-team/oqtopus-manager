"""Routes for the backend .env file editor (view, lock, save, download)."""

from oqtopus_manager.routers._dotenv_routes import make_dotenv_router

router, api_router = make_dotenv_router(
    html_url_prefix="/backend",
    api_url_prefix="/api/backend",
    tags=["backend"],
    release_diff_raw_url=(
        "https://raw.githubusercontent.com/oqtopus-team/oqtopus-cli"
        "/main/templates/backend/config/.env"
    ),
    release_diff_display_url=(
        "https://github.com/oqtopus-team/oqtopus-cli"
        "/blob/main/templates/backend/config/.env"
    ),
)
