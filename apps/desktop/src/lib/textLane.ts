/** A horizontal band with text pinned at one or both ends and a quantity
 *  drawn through the space between: the slider (label · value) and the
 *  composer's working strip (label · nothing) are the same shape.
 *
 *  Both protect their type the same way — anything painted in the band fades
 *  out before it reaches a text lane — so the rule lives here once instead of
 *  being re-derived per surface. Callers differ only in `fadeDistance`: a
 *  slider marker is a hairline and clears in a few px, while the strip's
 *  field is continuous and needs a long ramp or the cut reads as an edge. */
export function textLaneOpacity(
  position: number,
  laneEnd: number,
  laneStart: number,
  fadeDistance: number,
): number {
  if (position <= laneEnd || position >= laneStart) return 0;
  if (fadeDistance <= 0) return 1;
  const clearance = Math.min(position - laneEnd, laneStart - position) / fadeDistance;
  return clearance < 0 ? 0 : clearance > 1 ? 1 : clearance;
}
