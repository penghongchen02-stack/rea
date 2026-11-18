"""Every paper bot by key: the seven report bots, their v2 fixes, and the
simple idea bots. The one owner of which bots exist; the engines, audit and
reports read it from here."""
from __future__ import annotations

from .bots import FIXED, ORIGINALS, Bot
from .simple_bots import RUNNING
from .variants import VARIANTS

BOTS: dict[str, Bot] = {b.key: b for b in ORIGINALS + FIXED + RUNNING + VARIANTS}
