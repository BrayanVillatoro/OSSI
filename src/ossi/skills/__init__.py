"""Imports every module in this folder so its commands get registered."""

import importlib
import pkgutil

for module in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{module.name}")
