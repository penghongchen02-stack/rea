"""Bots taken off on 2026-11-19 at about 18:40 UTC because they were not working
or not making money (the user asked to remove them). Their history stays in the
ledger; the engine and the reports no longer run them. Delete a key to bring it back.

LOSING: below $1,000 in total and losing on settled matches, over 15+ settled.
IDLE:   no bet at all since they were added (hours of live games).
Kept although losing: s15-x07, the morning bot of six clock bots.
"""
LOSING = (
    "Live_a01-sport-switcher",   # -119 total, -105 settled over 56
    "Live_a02-slow-sports",   # -111 total, -88 settled over 56
    "Live_a03-busy-games",   # -187 total, -161 settled over 51
    "Live_a10-self-hours",   # -111 total, -88 settled over 56
    "Live_a13-price-lifetime",   # -89 total, -91 settled over 54
    "Live_a14-win-rate-leash",   # -84 total, -93 settled over 47
    "Live_a15-size-by-sport",   # -149 total, -137 settled over 56
    "Live_big-jump",   # -74 total, -70 settled over 43
    "Live_g11-reserve",   # -111 total, -88 settled over 56
    "Live_n02-snipe",   # -4 total, -0 settled over 17
    "Live_n04-spread-snap",   # -22 total, -25 settled over 17
    "Live_n11-open-interest",   # -130 total, -90 settled over 61
    "Live_n27-range",   # -6 total, -32 settled over 41
    "Live_n31-fake-move",   # -26 total, -17 settled over 40
    "Live_n40-calibration",   # -205 total, -171 settled over 45
    "Live_n43-best-per-sport",   # -295 total, -269 settled over 60
    "Live_n44-best-vs-worst",   # -124 total, -103 settled over 64
    "Live_n46-rounding",   # -417 total, -491 settled over 61
    "Live_tn-maker-2c",   # -97 total, -109 settled over 56
    "Live_tn-maker-3c",   # -132 total, -125 settled over 49
    "Live_tn-maker-quiet",   # -28 total, -32 settled over 17
    "Live_ud-20to45",   # -6 total, -17 settled over 30
    "Live_ud-2usd",   # -70 total, -89 settled over 50
    "Live_ud-jump1",   # -37 total, -48 settled over 50
    "Live_ud-jump25",   # -16 total, -27 settled over 47
    "Live_ud-tp10-sl10",   # -14 total, -12 settled over 50
    "Live_ud-tp15-sl10",   # -25 total, -24 settled over 50
    "Live_ud-tp20-sl15",   # -18 total, -20 settled over 50
    "Live_ud-tp25-sl20",   # -17 total, -21 settled over 50
    "Live_ud-tp5-sl5",   # -3 total, -4 settled over 50
    "a01-sport-switcher",   # -138 total, -120 settled over 57
    "a02-slow-sports",   # -128 total, -87 settled over 57
    "a03-busy-games",   # -63 total, -38 settled over 52
    "a05-biggest-gap",   # -107 total, -88 settled over 44
    "a07-price-step",   # -293 total, -235 settled over 57
    "a10-self-hours",   # -128 total, -87 settled over 57
    "a14-win-rate-leash",   # -128 total, -87 settled over 57
    "a15-size-by-sport",   # -138 total, -119 settled over 57
    "big-jump",   # -98 total, -109 settled over 44
    "ferrarichampions2026",   # -67 total, -51 settled over 587
    "g11-reserve",   # -128 total, -87 settled over 57
    "g12-clock",   # -119 total, -100 settled over 51
    "kch123",   # -214 total, -211 settled over 59
    "n04-spread-snap",   # -13 total, -16 settled over 20
    "n06-deep-book",   # -84 total, -59 settled over 59
    "n10-aggressive",   # -48 total, -47 settled over 42
    "n11-open-interest",   # -52 total, -16 settled over 63
    "n29-game-high",   # -99 total, -33 settled over 34
    "n37-split",   # -146 total, -96 settled over 49
    "n40-calibration",   # -170 total, -138 settled over 49
    "n42-low-fee",   # -201 total, -187 settled over 59
    "n43-best-per-sport",   # -368 total, -339 settled over 60
    "n44-best-vs-worst",   # -353 total, -330 settled over 64
    "n45-good-fills",   # -154 total, -113 settled over 63
    "n46-rounding",   # -181 total, -392 settled over 63
    "n47-scale-in",   # -63 total, -31 settled over 64
    "n48-half-lock",   # -34 total, -73 settled over 32
    "randomgamebet",   # -132 total, -130 settled over 62
    "randomgamebet-v2",   # -43 total, -45 settled over 31
    "s02-sportsbook-proxy",   # -68 total, -74 settled over 19
    "s07-underpriced-pair",   # -179 total, -145 settled over 446
    "s09-two-sided-maker",   # -553 total, -584 settled over 282
    "s10-quiet-maker",   # -113 total, -106 settled over 198
    "s12-volume-maker",   # -509 total, -525 settled over 281
    "s13-inventory-maker",   # -408 total, -438 settled over 314
    "s14-overreaction-fade",   # -184 total, -184 settled over 42
    "s16-garbage-time",   # -12 total, -10 settled over 245
    "s19-x03",   # -493 total, -637 settled over 161
    "s19-x04",   # -844 total, -908 settled over 114
    "s38-kelly",   # -14 total, -13 settled over 403
    "s41-streak-cooler",   # -26 total, -28 settled over 404
    "s42-sport-budget",   # -109 total, -125 settled over 360
    "sinenooneei",   # -728 total, -726 settled over 212
    "sinenooneei-v2",   # -344 total, -294 settled over 238
    "swisstony",   # -132 total, -95 settled over 429
    "swisstony-v2",   # -128 total, -87 settled over 277
    "swisstony-x02",   # -90 total, -53 settled over 292
    "swisstony-x03",   # -98 total, -80 settled over 254
    "swisstony-x04",   # -156 total, -97 settled over 311
    "swisstony-x06",   # -56 total, -31 settled over 453
    "swisstony-x07",   # -89 total, -25 settled over 220
    "swisstony-x09",   # -310 total, -287 settled over 357
    "swisstony-x10",   # -162 total, -124 settled over 334
    "tn-maker-1c",   # -8 total, -102 settled over 63
    "tn-maker-quiet",   # -76 total, -76 settled over 23
    "ud-20to45",   # -7 total, -15 settled over 32
    "ud-2usd",   # -74 total, -91 settled over 52
    "ud-jump1",   # -40 total, -49 settled over 52
    "ud-jump25",   # -32 total, -41 settled over 47
    "ud-tp10-sl10",   # -14 total, -11 settled over 52
    "ud-tp15-sl10",   # -22 total, -23 settled over 52
    "ud-tp20-sl15",   # -16 total, -19 settled over 52
    "ud-tp25-sl20",   # -17 total, -18 settled over 52
    "ud-tp5-sl5",   # -2 total, -5 settled over 52
)
IDLE = (
    "Live_g01-fights",
    "Live_g04-mlb",
    "Live_g08-draw",
    "Live_late-underdog",
    "Live_n05-empty-shelf",
    "Live_n15-spread-lag",
    "Live_n16-three-way",
    "Live_n17-womens-dog",
    "Live_n20-player-form",
    "Live_n21-strong-discount",
    "Live_n22-league-mood",
    "Live_n24-tired",
    "Live_n25-rematch",
    "Live_n26-hot-tournament",
    "Live_n28-calm-favourite",
    "Live_n33-steam-fade",
    "g01-fights",
    "g04-mlb",
    "g08-draw",
    "late-underdog",
    "n05-empty-shelf",
    "n15-spread-lag",
    "n16-three-way",
    "n17-womens-dog",
    "n20-player-form",
    "n21-strong-discount",
    "n24-tired",
    "n25-rematch",
    "n26-hot-tournament",
    "n28-calm-favourite",
    "n33-steam-fade",
)

# Taken off on 2026-11-21 at about 17:15 UTC at the user's request.
LOSING_NOV21 = (
    "n27-range",          # -929 settled over 319, $64 left of $1,000
    "Live_tn-maker-1c",   # -493 settled over 299
)

RETIRED = frozenset(LOSING + IDLE + LOSING_NOV21)
