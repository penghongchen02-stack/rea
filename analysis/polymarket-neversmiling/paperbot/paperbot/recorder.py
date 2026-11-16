"""Tape recorder (user, 11-16 07:50Z): saves what the engine sees, so any bot can later be replayed against the real
quotes. Nothing here changes a bot or a fill; the feeds call in and a writer thread appends to disk.

Files: <data>/tape/YYYYMMDD/HH.jsonl.gz, one JSON array a line, times in Unix seconds as this machine received them:

  ["m", ts, slug, asset, horizon, kind, start, end, p2b, settle_px, strike, r_lo, r_hi, fee_coef, market_type]
        a market's terms; written before its first book line in every file and again whenever a term changes
        (the starting line appears a few seconds into a window, the settlement price after its end)
  ["b", ts, slug, [[px, qty], ...] bids best first, [[px, qty], ...] offers best first, shares_traded, last_px, state]
        an order book as received (stream or REST), all levels up to LEVELS a side; a message that repeats the book
        and the traded total already written for that market is skipped
  ["s", ts, asset, px]    a Coinbase tick
  ["i", sec, asset, px]   a new second of the settlement index (the 60-second average of BRTI that Polymarket US publishes)

A file is appended to after an engine restart (gzip allows several members in one file). Recording stops, with one
warning in the log, while the disk has less than MIN_FREE_GB left, and resumes when there is room again. The tape is
not committed: it is far too large for the repository and lives only as long as this machine does.
"""
import os, time, json, gzip, queue, shutil, logging, threading

log = logging.getLogger("tape")
LEVELS = 25; MIN_FREE_GB = 5.0; FLUSH = 20.0
REC = None          # the engine's recorder, or None: feeds call the module functions below, which do nothing without one


class Recorder:
    def __init__(self, dir):
        self.dir = dir; self.q = queue.SimpleQueue(); self.last_book = {}; self.terms = {}; self.file_slugs = set()
        self.lines = 0; self.bytes = 0; self.paused = False; self.started = time.time(); self.hour = None; self.f = None
        threading.Thread(target=self.run, daemon=True, name="tape").start()

    def status(self):
        return {"lines": self.lines, "paused": self.paused, "queue": self.q.qsize(), "dir": self.dir}

    @staticmethod
    def _terms(m):
        return (m.slug, m.asset, m.horizon, m.kind, m.start, m.end, m.p2b, m.settle_px, m.strike, m.r_lo, m.r_hi, m.fee_coef, m.market_type)

    def market(self, m, now=None):
        t = self._terms(m)
        if self.terms.get(m.slug) == t and m.slug in self.file_slugs: return
        self.terms[m.slug] = t; self.file_slugs.add(m.slug); self.q.put(["m", round(now or time.time(), 3), *t])

    def book(self, m):
        key = (tuple(m.bids[:LEVELS]), tuple(m.offers[:LEVELS]), m.shares_traded)
        if self.last_book.get(m.slug) == key and m.slug in self.file_slugs: return
        self.last_book[m.slug] = key; self.market(m, m.book_ts)
        self.q.put(["b", round(m.book_ts, 3), m.slug, [list(x) for x in key[0]], [list(x) for x in key[1]], m.shares_traded, m.last_px, m.state])

    def forget(self, slug):
        self.last_book.pop(slug, None); self.terms.pop(slug, None)

    def spot(self, a, px, ts): self.q.put(["s", round(ts, 3), a, px])
    def index(self, a, sec, px): self.q.put(["i", sec, a, px])

    def _open(self, now):
        hour = time.strftime("%Y%m%d/%H", time.gmtime(now))
        if hour == self.hour: return
        if self.f: self.f.close()
        path = os.path.join(self.dir, hour + ".jsonl.gz"); os.makedirs(os.path.dirname(path), exist_ok=True)
        self.f = gzip.open(path, "at", compresslevel=6); self.hour = hour
        self.file_slugs = set()        # a new file repeats each market's terms and full book, so it can be read alone

    def run(self):
        last_flush = 0.0; last_check = 0.0
        while True:
            try:
                try: item = self.q.get(timeout=1.0)
                except queue.Empty: item = None
                now = time.time()
                if now - last_check > 60:
                    last_check = now; free = shutil.disk_usage(self.dir if os.path.isdir(self.dir) else os.path.dirname(self.dir)).free / 1e9
                    if (free < MIN_FREE_GB) != self.paused:
                        self.paused = free < MIN_FREE_GB
                        log.warning("tape %s: %.1f GB free", "paused" if self.paused else "resumed", free)
                if item is not None and not self.paused:
                    self._open(now); self.f.write(json.dumps(item, separators=(",", ":")) + "\n"); self.lines += 1
                if self.f and now - last_flush > FLUSH: self.f.flush(); last_flush = now
            except Exception as e:
                log.warning("tape: %s", e); time.sleep(5)


def start(dir):
    global REC
    if REC is None and os.environ.get("PAPERBOT_TAPE", "1") != "0": REC = Recorder(dir); log.info("tape recording to %s", dir)
    return REC

def book(m):
    if REC: REC.book(m)
def market(m):
    if REC: REC.market(m)
def forget(slug):
    if REC: REC.forget(slug)
def spot(a, px, ts):
    if REC: REC.spot(a, px, ts)
def index(a, sec, px):
    if REC: REC.index(a, sec, px)


def read(path):
    """Yield the lines of one tape file (for a replay)."""
    with gzip.open(path, "rt") as f:
        for line in f:
            try: yield json.loads(line)
            except ValueError: continue          # a line cut off by a kill
