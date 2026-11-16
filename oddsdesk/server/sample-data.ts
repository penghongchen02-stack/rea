/**
 * Simulated odds feed. Every team, sportsbook and price here is fictional.
 * Prices are derived from a hidden "true" probability per market, then each
 * book adds its own margin and noise; occasional mispricings produce +EV and
 * arbitrage opportunities. `tick()` moves the market like a live feed would.
 */
import { decimalToAmerican, probabilityToDecimal } from "../shared/odds-math.js";
import type { Book, MarketKind, MarketLine, SportEvent, Sport } from "./types.js";

export const BOOKS: Book[] = [
  { id: "sharpline", name: "Sharpline", sharp: true },
  { id: "northbet", name: "NorthBet", sharp: false },
  { id: "parlay-palace", name: "Parlay Palace", sharp: false },
  { id: "goldline", name: "Goldline", sharp: false },
  { id: "bluechip", name: "BlueChip Sports", sharp: false },
  { id: "fastodds", name: "FastOdds", sharp: false },
];

export const SPORTS: Sport[] = [
  { id: "football", name: "Pro Football", threeWay: false, spread: 6.5, total: 44.5 },
  { id: "basketball", name: "Pro Basketball", threeWay: false, spread: 5.5, total: 224.5 },
  { id: "baseball", name: "Pro Baseball", threeWay: false, spread: 1.5, total: 8.5 },
  { id: "hockey", name: "Pro Hockey", threeWay: false, spread: 1.5, total: 6.0 },
  { id: "soccer", name: "Premier Soccer", threeWay: true, spread: 0.5, total: 2.5 },
];

const TEAMS: Record<string, string[]> = {
  football: ["Harbor City Gulls", "Red Mesa Coyotes", "Lakeport Anchors", "Ironvale Forge", "Summit Peaks", "Coastline Marlins", "Prairie Bison", "Granite Falls Rams"],
  basketball: ["Metro Comets", "Bayside Sharks", "Desert Suns", "Riverton Rapids", "Northgate Wolves", "Capital Monarchs", "Pinecrest Owls", "Silver Bay Storm"],
  baseball: ["Elm Street Robins", "Canyon Diamondbacks", "Port Royal Sailors", "Highland Hawks", "Copper City Miners", "Bay Bridge Gulls"],
  hockey: ["Frostburg Blizzard", "Lakeshore Loons", "Steel Valley Hammers", "Glacier Bay Orcas", "Timberline Lynx", "Harbor Kings"],
  soccer: ["Westmoor FC", "Kingsbridge United", "Ashford Town", "Riverside Athletic", "Oakham City", "Marlow Rovers"],
};

/** Minimum sum of implied probabilities a single book offers (1% hold). */
const MIN_HOLD = 1.01;

const BOOK_MARGIN: Record<string, number> = {
  sharpline: 0.025,
  northbet: 0.045,
  "parlay-palace": 0.06,
  goldline: 0.05,
  bluechip: 0.045,
  fastodds: 0.055,
};

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
  trueProbabilities: number[];
}

export class SampleFeed {
  private readonly rand: () => number;
  private readonly events: SportEvent[] = [];
  private readonly markets: HiddenMarket[] = [];
  private versionCounter = 0;
  private updatedAtMs: number;

  constructor(seed = Date.now(), private readonly now: () => number = Date.now) {
    this.rand = mulberry32(seed);
    this.updatedAtMs = now();
    this.generate();
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

  /** Drift true probabilities and re-price a share of the books. */
  tick(): void {
    for (const market of this.markets) {
      if (this.rand() > 0.35) continue;
      market.trueProbabilities = drift(market.trueProbabilities, this.rand);
      this.price(market);
    }
    this.versionCounter++;
    this.updatedAtMs = this.now();
  }

  private generate(): void {
    const start = this.now();
    for (const sport of SPORTS) {
      const teams = shuffle([...(TEAMS[sport.id] ?? [])], this.rand);
      for (let i = 0; i + 1 < teams.length; i += 2) {
        const event: SportEvent = {
          id: `${sport.id}-${i / 2 + 1}`,
          sportId: sport.id,
          home: teams[i]!,
          away: teams[i + 1]!,
          startTime: new Date(start + (2 + Math.floor(this.rand() * 72)) * 3_600_000).toISOString(),
        };
        this.events.push(event);
        const homeStrength = 0.3 + this.rand() * 0.4;
        this.addMarket(event, "moneyline", null, sport.threeWay
          ? normalise([homeStrength * 0.8, (1 - homeStrength) * 0.8, 0.26])
          : [homeStrength, 1 - homeStrength], sport.threeWay
          ? [event.home, event.away, "Draw"]
          : [event.home, event.away]);
        const fav = homeStrength >= 0.5 ? -sport.spread : sport.spread;
        this.addMarket(event, "spread", fav, jitterPair(this.rand), [event.home, event.away]);
        this.addMarket(event, "total", sport.total, jitterPair(this.rand), ["Over", "Under"]);
      }
    }
    this.events.sort((a, b) => a.startTime.localeCompare(b.startTime));
  }

  private addMarket(event: SportEvent, kind: MarketKind, point: number | null, probs: number[], outcomes: string[]): void {
    const market: HiddenMarket = {
      line: { id: `${event.id}:${kind}`, eventId: event.id, kind, point, outcomes, prices: {} },
      trueProbabilities: probs,
    };
    this.price(market);
    this.markets.push(market);
  }

  private price(market: HiddenMarket): void {
    for (const book of BOOKS) {
      // Occasionally a book does not offer the market at all.
      if (!book.sharp && this.rand() < 0.06) {
        market.line.prices[book.id] = null;
        continue;
      }
      const margin = BOOK_MARGIN[book.id] ?? 0.05;
      const noise = book.sharp ? 0.004 : 0.012;
      // At most one outcome per book is mispriced, so a single book never offers an arbitrage against itself.
      const mispricedIndex = !book.sharp && this.rand() < 0.09
        ? Math.floor(this.rand() * market.trueProbabilities.length)
        : -1;
      const implied = market.trueProbabilities.map((p, index) => {
        let q = p * (1 + margin) + (this.rand() - 0.5) * noise;
        if (index === mispricedIndex) q -= 0.02 + this.rand() * 0.025; // stale or boosted price
        return q;
      });
      // A book always keeps some hold on its own market.
      const total = implied.reduce((a, b) => a + b, 0);
      const scale = total < MIN_HOLD ? MIN_HOLD / total : 1;
      market.line.prices[book.id] = implied.map((q) =>
        decimalToAmerican(probabilityToDecimal(Math.min(0.97, Math.max(0.03, q * scale)))),
      );
    }
  }
}

function drift(probs: number[], rand: () => number): number[] {
  return normalise(probs.map((p) => Math.min(0.92, Math.max(0.08, p + (rand() - 0.5) * 0.02))));
}

function jitterPair(rand: () => number): number[] {
  const p = 0.47 + rand() * 0.06;
  return [p, 1 - p];
}

function normalise(values: number[]): number[] {
  const sum = values.reduce((a, b) => a + b, 0);
  return values.map((v) => v / sum);
}

function shuffle<T>(items: T[], rand: () => number): T[] {
  for (let i = items.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [items[i], items[j]] = [items[j]!, items[i]!];
  }
  return items;
}
