"""The seven sports wallets analysed in the PR&R trader reports.

Addresses come from the report pages, except kch123, whose report shows no
address; that one is the Polymarket profile named exactly "kch123" (inferred).
"""
from dataclasses import dataclass

REPORTS = "https://www.polyresearchrobotics.com/reports/"


@dataclass(frozen=True)
class SourceWallet:
    key: str
    address: str
    summary: str
    address_inferred: bool = False

    @property
    def report_url(self) -> str:
        return REPORTS + self.key


WALLETS: dict[str, SourceWallet] = {
    w.key: w
    for w in [
        SourceWallet("ferrarichampions2026", "0xfe787d2da716d60e8acff57fb87eb13cd4d10319",
                     "Both-sides spread capture with dominant-side tilt"),
        SourceWallet("swisstony", "0x204f72f35326db932158cba6adff0b9a1da95e14",
                     "Fair-value comparator, buy-only, hold to resolution"),
        SourceWallet("gamblingisallyourneed", "0x507e52ef684ca2dd91f90a9d26d149dd3288beae",
                     "Pseudo market maker with directional overlay, tennis-heavy"),
        SourceWallet("sinenooneei", "0x38337de21ff0bb0a11a40761507d51e318d633d1",
                     "Live directional tennis / MLB totals, probe then anchor"),
        SourceWallet("kch123", "0x6a72f61820b26b1fe4d956e17b6dc2a1ea3033ee",
                     "Buy-only football / college basketball, $0.40-$0.70", address_inferred=True),
        SourceWallet("geiyecapixie", "0xcae693bcf9696a2ebf0a62de767719b45f354f85",
                     "Live League of Legends leader, early exit"),
        SourceWallet("randomgamebet", "0x904ed7c7820a434ea9b11e7333ed69190d850bec",
                     "Esports both-sides spread capture"),
    ]
}
