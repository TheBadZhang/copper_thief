"""Copper Thief - a KiCad PCB action plugin.

Install this folder as::

    <KiCad user plugins>/copper_thief/

and restart KiCad.  The action appears in PCB Editor under Tools > External
Plugins and, when enabled, on the toolbar.
"""

from __future__ import annotations

import os

__version__ = "1.2.0"

_REGISTERED = False


def register() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    from .plugin import CopperThiefPlugin

    CopperThiefPlugin().register()
    _REGISTERED = True


try:
    if not os.environ.get("COPPER_THIEF_NO_REGISTER"):
        register()
except Exception:  # pragma: no cover - KiCad will log the traceback
    import traceback

    traceback.print_exc()
