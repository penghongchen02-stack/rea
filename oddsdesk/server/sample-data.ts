/**
 * Simulated odds feed. Every team, player, sportsbook and price here is fictional.
 *
 * Each market has a hidden "true" probability. Books quote it with their own
 * margin and noise, sometimes hang a line half a step off the market, and
 * occasionally misprice a side. On every tick the sharp book follows the true
 * price immediately while soft books catch up later, which produces the line
 * moves, stale prices, +EV spots, arbitrages and middles the tools look for.
 */
import { decimalToAmerican, probabilityToDecimal } from "../shared/odds-math.js";
import type { Book, MarketKind, MarketLine, Offer, Period, Sport, SportEvent } from "./types.js";

export const BOOKS: Book[] = [
  { id: "sharpline", name: "Sharpline", sharp: true },
  { id: "northbet", name: "NorthBet", sharp: false },
  { id: "parlay-palace", name: "Parlay Palace", sharp: false },
  { id: "goldline", name: "Goldline", sharp: false },
  { id: "bluechip", name: "BlueChip Sports", sharp: false },
  { id: "fastodds", name: "FastOdds", sharp: false },
  { id: "lucky-lane", name: "Lucky Lane", sharp: false },
  { id: "summit-sports", name: "Summit Sports", sharp: false },
];

export const SPORTS: Sport[] = [
  { id: "football", name: "Pro Football", threeWay: false, spread: 6.5, total: 44.5, periods: ["h1", "q1"] },
  { id: "basketball", name: "Pro Basketball", threeWay: false, spread: 5.5, total: 224.5, periods: ["h1", "q1"] },
  { id: "baseball", name: "Pro Baseball", threeWay: false, spread: 1.5, total: 8.5, periods: ["f5"] },
  { id: "hockey", name: "Pro Hockey", threeWay: false, spread: 1.5, total: 6, periods: ["p1"] },
  { id: "soccer", name: "Premier Soccer", threeWay: true, spread: 0.5, total: 2.5, periods: ["h1"] },
];

const TEAMS: Record<string, string[]> = {
  football: ["Harbor City Gulls", "Red Mesa Coyotes", "Lakeport Anchors", "Ironvale Forge", "Summit Peaks", "Coastline Marlins", "Prairie Bison", "Granite Falls Rams"],
  basketball: ["Metro Comets", "Bayside Sharks", "Desert Suns", "Riverton Rapids", "Northgate Wolves", "Capital Monarchs", "Pinecrest Owls", "Silver Bay Storm"],
  baseball: ["Elm Street Robins", "Canyon Diamondbacks", "Port Royal Sailors", "Highland Hawks", "Copper City Miners", "Bay Bridge Gulls"],
  hockey: ["Frostburg Blizzard", "Lakeshore Loons", "Steel Valley Hammers", "Glacier Bay Orcas", "Timberline Lynx", "Harbor Kings"],
  soccer: ["Westmoor FC", "Kingsbridge United", "Ashford Town", "Riverside Athletic", "Oakham City", "Marlow Rovers"],
};

interface PropSpec {
  type: string;
  /** Typical line range. */
  min: number;
  max: number;
  /** Distance between adjacent lines a book might hang. */
  step: number;
  /** Change in Over probability per step of line. */
  probPerStep: number;
}

/** Player props per sport; each team gets one player per spec. */
const PROPS: Record<string, PropSpec[]> = {
  football: [
    { type: "Passing Yards", min: 205, max: 285, step: 1, probPerStep: 0.012 },
    { type: "Rushing Yards", min: 45, max: 95, step: 1, probPerStep: 0.016 },
    { type: "Receiving Yards", min: 35, max: 85, step: 1, probPerStep: 0.016 },
  ],
  basketball: [
    { type: "Points", min: 14, max: 30, step: 1, probPerStep: 0.05 },
    { type: "Rebounds", min: 4, max: 12, step: 1, probPerStep: 0.09 },
    { type: "Assists", min: 3, max: 9, step: 1, probPerStep: 0.1 },
  ],
  baseball: [
    { type: "Strikeouts", min: 4, max: 8, step: 1, probPerStep: 0.12 },
    { type: "Total Bases", min: 1, max: 2, step: 1, probPerStep: 0.18 },
  ],
  hockey: [
    { type: "Shots on Goal", min: 2, max: 4, step: 1, probPerStep: 0.15 },
  ],
  soccer: [
    { type: "Shots on Target", min: 0, max: 2, step: 1, probPerStep: 0.18 },
  ],
};

const FIRST_NAMES = ["Avery", "Jalen", "Marcus", "Theo", "Dante", "Rowan", "Elias", "Malik", "Cyrus", "Isaiah", "Nolan", "Quinn", "Tobias", "Silas", "Darius", "Emmett", "Kai", "Luca", "Reid", "Xavier", "Bram", "Caleb", "Felix", "Grady"];
const LAST_NAMES = ["Okafor", "Lindqvist", "Marsh", "Vance", "Achebe", "Delgado", "Hollis", "Pryor", "Navarro", "Whitlock", "Brannigan", "Castellano", "Duquette", "Ferro", "Galloway", "Hargrove", "Ibarra", "Kowalczyk", "Lombard", "Mercer", "Ostrowski", "Quarles", "Rainier", "Sutcliffe"];

/** Per-book margin (sum of implied probabilities minus one) on standard markets. */
const BOOK_MARGIN: Record<string, number> = {
  sharpline: 0.025,
  northbet: 0.045,
  "parlay-palace": 0.06,
  goldline: 0.05,
  bluechip: 0.045,
  fastodds: 0.055,
  "lucky-lane": 0.065,
  "summit-sports": 0.05,
};

/** Futures carry far more hold than game markets. */
const FUTURES_MARGIN_SHARP = 0.08;
const FUTURES_MARGIN_SOFT = 0.22;

/** Minimum sum of implied probabilities a single book offers (1% hold). */
const MIN_HOLD = 1.01;

/** Small deterministic PRNG so tests and restarts can reproduce a feed. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

interface HiddenMarket {
  line: MarketLine;
  /** True probability per outcome at the main line. */
  probs: number[];
  /** Line increment books move by; null when the market has no line. */
  step: number | null;
  /** How much the first outcome's probability moves per step of line. */
  probPerStep: number;
  /** +1 when a higher point helps the first outcome (spread), -1 when it hurts (Over). */
  pointSign: 1 | -1;
  /** Steps away from the main line each book hangs. */
  bookShift: Record<string, number>;
  /** Hidden mispriced outcome per book, or -1. */
  mispriced: Record<string, number>;
}

interface PeriodSpec {
  period: Period;
  moneyline: boolean;
  spread: number | null;
  total: number | null;
}

function periodSpecs(sport: Sport): PeriodSpec[] {
  const full: PeriodSpec = { period: "full", moneyline: true, spread: sport.spread, total: sport.total };
  const table: Record<string, PeriodSpec[]> = {
    football: [full, { period: "h1", moneyline: true, spread: 3.5, total: 21.5 }, { period: "q1", moneyline: false, spread: 0.5, total: 9.5 }],
    basketball: [full, { period: "h1", moneyline: true, spread: 3, total: 112.5 }, { period: "q1", moneyline: true, spread: 1.5, total: 56.5 }],
    baseball: [full, { period: "f5", moneyline: true, spread: 0.5, total: 4.5 }],
    hockey: [full, { period: "p1", moneyline: false, spread: null, total: 1.5 }],
    soccer: [full, { period: "h1", moneyline: true, spread: null, total: 1 }],
  };
  return table[sport.id] ?? [full];
}

export class SampleFeed {
  private readonly rand: () => number;
  private readonly events: SportEvent[] = [];
  private readonly markets: HiddenMarket[] = [];
  private versionCounter = 0;
  private updatedAtMs: number;

  /**
   * @param warmupTicks ticks simulated before the feed opens, spread over the
   *   past hour, so line-movement history exists from the start.
   */
  constructor(seed = Date.now(), private readonly now: () => number = Date.now, warmupTicks = 24) {
    this.rand = mulberry32(seed);
    const start = now();
    this.updatedAtMs = start - warmupTicks * 150_000;
    this.generate(start);
    for (let i = warmupTicks - 1; i >= 0; i--) this.tick(start - i * 150_000);
    this.versionCounter = 0;
  }

  get version(): number {
    return this.versionCounter;
  }

  get updatedAt(): string {
    return new Date(this.updatedAtMs).toISOString();
  }

  listEvents(): readonly SportEvent[] {
    return this.events;
  }

  listMarkets(): readonly MarketLine[] {
    return this.markets.map((m) => m.line);
  }

  /** Move true prices; the sharp book reprices at once, soft books lag. */
  tick(atMs: number = this.now()): void {
    const stamp = new Date(atMs).toISOString();
    for (const market of this.markets) {
      let moved = false;
      if (this.rand() < 0.008) {
        market.probs = steam(market.probs, this.rand);
        moved = true;
      } else if (this.rand() < 0.3) {
        market.probs = drift(market.probs, this.rand);
        moved = true;
      }
      for (const bookId of Object.keys(market.line.offers)) {
        const sharp = bookId === "sharpline";
        if (sharp ? !moved : this.rand() > 0.22) continue;
        // Books keep the line they opened on; their prices move.
        if (!sharp && this.rand() < 0.15) {
          market.mispriced[bookId] = this.rand() < 0.04 ? Math.floor(this.rand() * market.probs.length) : -1;
        }
        this.quote(market, bookId, stamp);
      }
    }
    this.versionCounter++;
    this.updatedAtMs = atMs;
  }

  private generate(startMs: number): void {
    const stamp = new Date(startMs).toISOString();
    const usedNames = new Set<string>();
    for (const sport of SPORTS) {
      const teams = shuffle([...(TEAMS[sport.id] ?? [])], this.rand);
      for (let i = 0; i + 1 < teams.length; i += 2) {
        const home = teams[i]!;
        const away = teams[i + 1]!;
        const event: SportEvent = {
          id: `${sport.id}-${i / 2 + 1}`,
          sportId: sport.id,
          kind: "game",
          title: `${away} @ ${home}`,
          home,
          away,
          startTime: new Date(startMs + (2 + Math.floor(this.rand() * 72)) * 3_600_000).toISOString(),
        };
        this.events.push(event);
        const homeStrength = 0.3 + this.rand() * 0.4;
        const favSign = homeStrength >= 0.5 ? -1 : 1;

        for (const spec of periodSpecs(sport)) {
          if (spec.moneyline) {
            const threeWay = sport.threeWay;
            const draw = spec.period === "full" ? 0.26 : 0.4;
            const probs = threeWay
              ? normalise([homeStrength * (1 - draw), (1 - homeStrength) * (1 - draw), draw])
              : [homeStrength, 1 - homeStrength];
            this.add(event, { kind: "moneyline", period: spec.period, outcomes: threeWay ? [home, away, "Draw"] : [home, away], probs }, stamp);
          }
          if (spec.spread !== null) {
            this.add(event, {
              kind: "spread", period: spec.period, outcomes: [home, away], probs: nearEven(this.rand),
              point: favSign * spec.spread, step: 0.5, probPerStep: sport.id === "basketball" ? 0.025 : 0.035, pointSign: 1,
            }, stamp);
          }
          if (spec.total !== null) {
            this.add(event, {
              kind: "total", period: spec.period, outcomes: ["Over", "Under"], probs: nearEven(this.rand),
              point: spec.total, step: 0.5, probPerStep: spec.total > 50 ? 0.02 : 0.06, pointSign: -1,
            }, stamp);
          }
        }

        if (sport.id !== "soccer") {
          for (const team of [home, away]) {
            const teamTotal = roundHalf(sport.total / 2 + (team === home ? 1 : -1) * (homeStrength - 0.5) * sport.total * 0.2);
            this.add(event, {
              kind: "team_total", period: "full", participant: team, outcomes: ["Over", "Under"], probs: nearEven(this.rand),
              point: teamTotal, step: 0.5, probPerStep: sport.total > 50 ? 0.03 : 0.08, pointSign: -1,
            }, stamp);
          }
        }

        for (const spec of PROPS[sport.id] ?? []) {
          for (const team of [home, away]) {
            const player = uniqueName(this.rand, usedNames);
            this.add(event, {
              kind: "player_prop", period: "full", participant: `${player} (${team})`, propType: spec.type, outcomes: ["Over", "Under"],
              probs: nearEven(this.rand), point: roundHalf(spec.min + this.rand() * (spec.max - spec.min)),
              step: spec.step, probPerStep: spec.probPerStep, pointSign: -1,
            }, stamp);
          }
        }
      }

      const future: SportEvent = {
        id: `${sport.id}-futures`,
        sportId: sport.id,
        kind: "future",
        title: `${sport.name} Champion`,
        home: null,
        away: null,
        startTime: new Date(startMs + 150 * 86_400_000).toISOString(),
      };
      this.events.push(future);
      const contenders = TEAMS[sport.id] ?? [];
      this.add(future, { kind: "future", period: "full", outcomes: [...contenders], probs: normalise(contenders.map(() => 0.3 + this.rand() ** 2 * 2)) }, stamp);
    }
    this.events.sort((a, b) => a.startTime.localeCompare(b.startTime));
  }

  private add(
    event: SportEvent,
    spec: {
      kind: MarketKind;
      period: Period;
      outcomes: string[];
      probs: number[];
      participant?: string;
      propType?: string;
      point?: number;
      step?: number;
      probPerStep?: number;
      pointSign?: 1 | -1;
    },
    stamp: string,
  ): void {
    const id = [event.id, spec.period, spec.kind, spec.participant, spec.propType].filter(Boolean).join(":").replace(/\s+/g, "-").toLowerCase();
    const market: HiddenMarket = {
      line: {
        id,
        eventId: event.id,
        kind: spec.kind,
        period: spec.period,
        participant: spec.participant ?? null,
        propType: spec.propType ?? null,
        outcomes: spec.outcomes,
        point: spec.point ?? null,
        offers: {},
        opening: {},
        changedAt: {},
      },
      probs: spec.probs,
      step: spec.step ?? null,
      probPerStep: spec.probPerStep ?? 0,
      pointSign: spec.pointSign ?? 1,
      bookShift: {},
      mispriced: {},
    };
    for (const book of BOOKS) {
      // Soft books skip some markets entirely, more often on props and short periods.
      const skip = book.sharp ? 0 : spec.kind === "player_prop" ? 0.3 : spec.period === "full" ? 0.05 : 0.15;
      if (this.rand() < skip) {
        market.line.offers[book.id] = null;
        market.line.opening[book.id] = null;
        continue;
      }
      market.bookShift[book.id] = book.sharp || market.step === null ? 0 : randomShift(this.rand);
      market.mispriced[book.id] = !book.sharp && this.rand() < 0.04 ? Math.floor(this.rand() * spec.outcomes.length) : -1;
      this.quote(market, book.id, stamp);
      market.line.opening[book.id] = market.line.offers[book.id] ?? null;
    }
    this.markets.push(market);
  }

  /** Price one book's quote from the hidden probabilities. */
  private quote(market: HiddenMarket, bookId: string, stamp: string): void {
    if (market.line.offers[bookId] === null) return;
    const sharp = bookId === "sharpline";
    const futures = market.line.kind === "future";
    const margin = futures ? (sharp ? FUTURES_MARGIN_SHARP : FUTURES_MARGIN_SOFT) : BOOK_MARGIN[bookId] ?? 0.05;
    const noise = sharp ? 0.004 : 0.012;
    const shift = market.bookShift[bookId] ?? 0;

    let probs = market.probs;
    let point = market.line.point;
    if (market.step !== null && point !== null && shift !== 0) {
      point = point + shift * market.step;
      const p0 = clamp(probs[0]! + market.pointSign * shift * market.probPerStep, 0.05, 0.95);
      probs = [p0, 1 - p0];
    }

    const misprice = market.mispriced[bookId] ?? -1;
    const implied = probs.map((p, index) => {
      const scale = probs.length > 3 ? Math.min(1, p * 3) : 1;
      let q = p * (1 + margin) + (this.rand() - 0.5) * noise * scale;
      if (index === misprice) q -= (0.02 + this.rand() * 0.025) * scale;
      return q;
    });
    const total = implied.reduce((a, b) => a + b, 0);
    const scaleUp = total < MIN_HOLD ? MIN_HOLD / total : 1;
    const offer: Offer = {
      point,
      prices: implied.map((q) => decimalToAmerican(probabilityToDecimal(clamp(q * scaleUp, 0.004, 0.97)))),
    };
    const previous = market.line.offers[bookId];
    if (!previous || previous.point !== offer.point || previous.prices.some((p, i) => p !== offer.prices[i])) {
      market.line.offers[bookId] = offer;
      market.line.changedAt[bookId] = stamp;
    }
  }
}

function randomShift(rand: () => number): number {
  const r = rand();
  return r < 0.12 ? -1 : r < 0.24 ? 1 : 0;
}

function drift(probs: number[], rand: () => number): number[] {
  return normalise(probs.map((p) => clamp(p + (rand() - 0.5) * 0.02 * Math.min(1, p * 3), 0.02, 0.95)));
}

/** A sharp move: one side gets steamed by several points of probability. */
function steam(probs: number[], rand: () => number): number[] {
  const target = Math.floor(rand() * probs.length);
  const size = 0.04 + rand() * 0.04;
  return normalise(probs.map((p, i) => clamp(i === target ? p + size : p, 0.02, 0.95)));
}

function nearEven(rand: () => number): number[] {
  const p = 0.46 + rand() * 0.08;
  return [p, 1 - p];
}

function normalise(values: number[]): number[] {
  const sum = values.reduce((a, b) => a + b, 0);
  return values.map((v) => v / sum);
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function roundHalf(value: number): number {
  return Math.floor(value) + 0.5;
}

function uniqueName(rand: () => number, used: Set<string>): string {
  for (;;) {
    const name = `${FIRST_NAMES[Math.floor(rand() * FIRST_NAMES.length)]} ${LAST_NAMES[Math.floor(rand() * LAST_NAMES.length)]}`;
    if (!used.has(name)) {
      used.add(name);
      return name;
    }
  }
}

function shuffle<T>(items: T[], rand: () => number): T[] {
  for (let i = items.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [items[i], items[j]] = [items[j]!, items[i]!];
  }
  return items;
}
