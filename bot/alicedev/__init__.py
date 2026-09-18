"""alicedev AstrBot plugin package.

All intra-package imports use the absolute ``alicedev.*`` form. ``bot/main.py``
inserts its own directory into ``sys.path`` before importing this package, so the
plugin directory (mounted at ``/AstrBot/data/plugins/alicedev``) exposes this
subpackage as the top-level ``alicedev`` module.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
