"""Every paper bot by key: the seven report bots, their v2 fixes, and the
simple idea bots. The one owner of which bots exist; the engines, audit and
reports read it from here."""
from __future__ import annotations

from .bots import FIXED, ORIGINALS, Bot
from .exits import EXITS
from .clock import CLOCKS
from .hours import HOURS
from .retired import RETIRED
from .live19 import LIVE19
from .simple_bots import RUNNING
from .livesim import TWINS
from .allsports import ALL15
from .fifty import FIFTY
from .gaps import GAPS
from .ideas import IDEAS6
from .tennis import TENNIS_BOTS
from .underdog import UD_VERSIONS, UNDERDOG
from .variants import VARIANTS

ALL_BOTS: dict[str, Bot] = {b.key: b for b in ORIGINALS + FIXED + RUNNING + VARIANTS + EXITS + TWINS + LIVE19 + UNDERDOG + TENNIS_BOTS + IDEAS6 + FIFTY + GAPS + ALL15 + UD_VERSIONS + CLOCKS + HOURS}
# Bots taken off for not working or losing (retired.py); ALL_BOTS keeps them for tests.
BOTS: dict[str, Bot] = {k: b for k, b in ALL_BOTS.items() if k not in RETIRED}
