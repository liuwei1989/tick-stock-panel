from app.api.notification_channels import router
from app.extensions import BACKEND_EXTENSION_API_VERSION

EXTENSION_ID = "notifications.channels"
EXTENSION_API_VERSION = BACKEND_EXTENSION_API_VERSION


def setup(registrar):
    registrar.include_router(router)
