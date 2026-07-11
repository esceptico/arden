import type { AreaDetail, AreasOverview } from "@/api/areas";
import type { Area } from "@/api/types";

export interface AreasDomainState {
  recordsById: Record<string, Area>;
  recordOrder: string[];
  overview: AreasOverview | null;
  detailByKey: Record<string, AreaDetail>;
  openAreaKey: string | null;
  loading: boolean;
}

export function createAreasDomainState(): AreasDomainState {
  return {
    overview: null,
    recordsById: {},
    recordOrder: [],
    detailByKey: {},
    openAreaKey: null,
    loading: false,
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
    loading: false,
  };
}

export function reduceDetailLoaded(state: AreasDomainState, detail: AreaDetail): AreasDomainState {
  return {
    ...state,
    detailByKey: { ...state.detailByKey, [detail.key]: detail },
    loading: false,
  };
}

export function reduceAskResolved(state: AreasDomainState, key: string, askId: string): AreasDomainState {
  const overview = state.overview
    ? {
        ...state.overview,
        focus: state.overview.focus.filter((a) => !(a.id === askId && a.area_key === key)),
      }
    : null;

  const detail = state.detailByKey[key]
    ? {
        ...state.detailByKey[key],
        asks: state.detailByKey[key].asks.filter((a) => a.id !== askId),
      }
    : undefined;

  const detailByKey = detail ? { ...state.detailByKey, [key]: detail } : state.detailByKey;

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

