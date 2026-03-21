"""
Configuration Package
=====================
Pipeline configuration and regional settings.
"""

from .pipeline_config import PipelineConfig

try:
    from .regional_config import RegionalConfig
    REGIONAL_CONFIG_AVAILABLE = True
except ImportError:
    REGIONAL_CONFIG_AVAILABLE = False
    RegionalConfig = None

__all__ = [
    'PipelineConfig',
    'RegionalConfig',
    'REGIONAL_CONFIG_AVAILABLE',
]
