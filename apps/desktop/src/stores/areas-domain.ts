import type { AreaDetail, AreasOverview } from "@/api/areas";
import type { Area } from "@/api/types";

export type AreasLoadPhase = "idle" | "loading" | "ready" | "error";

export interface AreasDomainState {
  recordsById: Record<string, Area>;
  recordOrder: string[];
  overview: AreasOverview | null;
  detailByKey: Record<string, AreaDetail>;
  detailPhaseByKey: Record<string, AreasLoadPhase>;
  openAreaKey: string | null;
  overviewPhase: AreasLoadPhase;
}

export function createAreasDomainState(): AreasDomainState {
  return {
    overview: null,
    recordsById: {},
    recordOrder: [],
    detailByKey: {},
    detailPhaseByKey: {},
    openAreaKey: null,
    overviewPhase: "idle",
  };
}

export function reduceRecordsLoaded(state: AreasDomainState, records: Area[]): AreasDomainState {
  return {
    ...state,
    recordsById: Object.fromEntries(records.map((record) => [record.area_id, record])),
    recordOrder: records.map((record) => record.area_id),
  };
}

export function reduceRecordUpserted(state: AreasDomainState, record: Area): AreasDomainState {
  const exists = Boolean(state.recordsById[record.area_id]);
  const overview = state.overview
    ? {
        ...state.overview,
        areas: state.overview.areas.map((area) =>
          area.key === record.area_id
            ? {
                ...area,
                title: record.name,
                page_path: record.page_path,
                autonomy: record.autonomy,
              }
            : area,
        ),
      }
    : null;
  const detail = state.detailByKey[record.area_id];
  const detailByKey = detail
    ? {
        ...state.detailByKey,
        [record.area_id]: {
          ...detail,
          title: record.name,
          page_path: record.page_path,
          autonomy: record.autonomy,
          attention: record.attention,
          interrupts: record.interrupts,
          paused: record.paused_at !== null,
        },
      }
    : state.detailByKey;
  return {
    ...state,
    recordsById: { ...state.recordsById, [record.area_id]: record },
    recordOrder: exists ? state.recordOrder : [record.area_id, ...state.recordOrder],
    overview,
    detailByKey,
  };
}

export function reduceRecordArchived(state: AreasDomainState, areaId: string): AreasDomainState {
  const recordsById = { ...state.recordsById };
  const detailByKey = { ...state.detailByKey };
  delete recordsById[areaId];
  delete detailByKey[areaId];
  return {
    ...state,
    recordsById,
    recordOrder: state.recordOrder.filter((id) => id !== areaId),
    overview: state.overview
      ? {
          ...state.overview,
          areas: state.overview.areas.filter((area) => area.key !== areaId),
          focus: state.overview.focus.filter((ask) => ask.area_key !== areaId),
        }
      : null,
    detailByKey,
    openAreaKey: state.openAreaKey === areaId ? null : state.openAreaKey,
  };
}

export function reduceOverviewLoaded(
  state: AreasDomainState,
  overview: AreasOverview,
): AreasDomainState {
  return {
    ...state,
    overview,
    overviewPhase: "ready",
  };
}

export function reduceDetailLoaded(state: AreasDomainState, detail: AreaDetail): AreasDomainState {
  return {
    ...state,
    detailByKey: { ...state.detailByKey, [detail.key]: detail },
    detailPhaseByKey: { ...state.detailPhaseByKey, [detail.key]: "ready" },
  };
}

export function reduceOverviewPhase(
  state: AreasDomainState,
  phase: AreasLoadPhase,
): AreasDomainState {
  return { ...state, overviewPhase: phase };
}

export function reduceDetailPhase(
  state: AreasDomainState,
  key: string,
  phase: AreasLoadPhase,
): AreasDomainState {
  return {
    ...state,
    detailPhaseByKey: { ...state.detailPhaseByKey, [key]: phase },
  };
}

/** Ask ids are globally unique (the server keys them in one flat store), so a
 *  resolution clears the ask wherever it is cached — no area key needed. */
export function reduceAskResolved(state: AreasDomainState, askId: string): AreasDomainState {
  const overview = state.overview
    ? {
        ...state.overview,
        focus: state.overview.focus.filter((a) => a.id !== askId),
      }
    : null;

  const detailByKey = Object.fromEntries(
    Object.entries(state.detailByKey).map(([key, detail]) => [
      key,
      detail.asks.some((a) => a.id === askId)
        ? { ...detail, asks: detail.asks.filter((a) => a.id !== askId) }
        : detail,
    ]),
  );

  return {
    ...state,
    overview,
    detailByKey,
  };
}

export function reduceOpenArea(state: AreasDomainState, key: string | null): AreasDomainState {
  return {
    ...state,
    openAreaKey: key,
  };
}
