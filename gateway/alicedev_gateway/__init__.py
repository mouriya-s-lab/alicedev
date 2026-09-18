"""alicedev's authenticated Paseo/report gateway."""

from .app import create_app
from .config import GatewayConfig

__all__ = ["GatewayConfig", "create_app"]
