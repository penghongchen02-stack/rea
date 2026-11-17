/**
 * Read-only tool endpoints shared by the Express API and the in-browser demo
 * backend, so both adapters answer every query identically.
 */
import { buildBoard, findArbs, findEv, findLowHold, findMiddles, findMoves } from "./analysis.js";
import { FREE_LIMITS, evMethodSchema, gate, marketFilterSchema, minEvSchema, minMoveSchema } from "./rules.js";
import { BOOKS, SPORTS, type SampleFeed } from "./sample-data.js";
import { PERIOD_LABEL, type User } from "./types.js";

export type Query = Record<string, unknown>;
export type ReadRoute = (query: Query, user: User | undefined) => unknown;

export function readRoutes(feed: SampleFeed): Record<string, ReadRoute> {
  const stamp = () => ({ updatedAt: feed.updatedAt, version: feed.version });
  const data = () => [feed.listEvents(), feed.listMarkets()] as const;

  return {
    "/api/meta": () => {
      const events = feed.listEvents();
      const sportOf = new Map(events.map((e) => [e.id, e.sportId]));
      const propTypes = (sportId: string) => [
        ...new Set(feed.listMarkets().filter((m) => m.propType && sportOf.get(m.eventId) === sportId).map((m) => m.propType!)),
      ];
      return {
        sampleData: true,
        sports: SPORTS.map(({ id, name, periods }) => ({
          id,
          name,
          periods: ["full", ...periods].map((p) => ({ id: p, name: PERIOD_LABEL[p as keyof typeof PERIOD_LABEL] })),
          propTypes: propTypes(id),
        })),
        events: events.filter((e) => e.kind === "game").map(({ id, sportId, title, startTime }) => ({ id, sportId, title, startTime })),
        books: BOOKS,
        freeLimits: FREE_LIMITS,
        ...stamp(),
      };
    },
    "/api/odds": (query) => ({ ...stamp(), rows: buildBoard(...data(), marketFilterSchema.parse(query)) }),
    "/api/ev": (query, user) => {
      const filter = marketFilterSchema.parse(query);
      const all = findEv(...data(), BOOKS, { ...filter, minEv: minEvSchema.parse(query.minEv), method: evMethodSchema.parse(query.method) });
      return { ...stamp(), ...gate(all, user, FREE_LIMITS.ev) };
    },
    "/api/arbs": (query, user) => ({ ...stamp(), ...gate(findArbs(...data(), marketFilterSchema.parse(query)), user, FREE_LIMITS.arbs) }),
    "/api/low-hold": (query, user) => ({ ...stamp(), ...gate(findLowHold(...data(), marketFilterSchema.parse(query)), user, FREE_LIMITS.lowHold) }),
    "/api/middles": (query, user) => ({ ...stamp(), ...gate(findMiddles(...data(), marketFilterSchema.parse(query)), user, FREE_LIMITS.middles) }),
    "/api/moves": (query, user) => {
      const filter = marketFilterSchema.parse(query);
      const all = findMoves(...data(), { ...filter, minChange: minMoveSchema.parse(query.minMove) });
      return { ...stamp(), ...gate(all, user, FREE_LIMITS.moves) };
    },
  };
}
