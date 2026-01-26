# Expose load_config for imports like: from core.config import load_config
from .loader import Config, load_config

__all__ = ["Config", "load_config"]
