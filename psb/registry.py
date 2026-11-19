"""Every paper bot by key: the seven report bots, their v2 fixes, and the
simple idea bots. The one owner of which bots exist; the engines, audit and
reports read it from here."""
from __future__ import annotations

from .bots import FIXED, ORIGINALS, Bot
from .exits import EXITS
from .live19 import LIVE19
from .simple_bots import RUNNING
from .livesim import TWINS
from .ideas import IDEAS6
from .tennis import TENNIS_BOTS
from .underdog import UNDERDOG
from .variants import VARIANTS

BOTS: dict[str, Bot] = {b.key: b for b in ORIGINALS + FIXED + RUNNING + VARIANTS + EXITS + TWINS + LIVE19 + UNDERDOG + TENNIS_BOTS + IDEAS6}
