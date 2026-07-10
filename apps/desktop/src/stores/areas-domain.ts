import type { AreaDetail, AreasOverview } from "@/api/areas";

export interface AreasDomainState {
  overview: AreasOverview | null;
  detailByKey: Record<string, AreaDetail>;
  openAreaKey: string | null;
  loading: boolean;
}

export function createAreasDomainState(): AreasDomainState {
  return {
    overview: null,
    detailByKey: {},
    openAreaKey: null,
    loading: false,
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

export function reduceAutonomyUpdated(
  state: AreasDomainState,
  key: string,
  autonomy: "observe" | "act",
): AreasDomainState {
  const detail = state.detailByKey[key];
  const detailByKey = detail
    ? { ...state.detailByKey, [key]: { ...detail, autonomy } }
    : state.detailByKey;

  const overview = state.overview
    ? {
        ...state.overview,
        areas: state.overview.areas.map((s) => (s.key === key ? { ...s, autonomy } : s)),
      }
    : null;

  return { ...state, detailByKey, overview };
}
