/** Helpers geométricos compartilhados pelas telas do evento. */

function _coordinate(point, index, names) {
  const raw = Array.isArray(point)
    ? point[index]
    : names.map(name => point?.[name]).find(value => value != null);
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

function _pointOnSegment(lat, lng, a, b) {
  const aLat = _coordinate(a, 0, ["lat", "latitude"]);
  const aLng = _coordinate(a, 1, ["lng", "lon", "longitude"]);
  const bLat = _coordinate(b, 0, ["lat", "latitude"]);
  const bLng = _coordinate(b, 1, ["lng", "lon", "longitude"]);
  if ([aLat, aLng, bLat, bLng].some(value => value == null)) return false;
  const cross = (lat - aLat) * (bLng - aLng) - (lng - aLng) * (bLat - aLat);
  if (Math.abs(cross) > 1e-10) return false;
  return lat >= Math.min(aLat, bLat) - 1e-10
    && lat <= Math.max(aLat, bLat) + 1e-10
    && lng >= Math.min(aLng, bLng) - 1e-10
    && lng <= Math.max(aLng, bLng) + 1e-10;
}

/** Ray casting com borda inclusiva. O polígono usa a ordem [lat, lng]. */
export function pointInPolygon(lat, lng, polygon) {
  lat = Number(lat);
  lng = Number(lng);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)
      || !Array.isArray(polygon) || polygon.length < 3) return false;

  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const a = polygon[j];
    const b = polygon[i];
    if (_pointOnSegment(lat, lng, a, b)) return true;
    const aLat = _coordinate(a, 0, ["lat", "latitude"]);
    const aLng = _coordinate(a, 1, ["lng", "lon", "longitude"]);
    const bLat = _coordinate(b, 0, ["lat", "latitude"]);
    const bLng = _coordinate(b, 1, ["lng", "lon", "longitude"]);
    if ([aLat, aLng, bLat, bLng].some(value => value == null)) continue;
    if ((aLat > lat) !== (bLat > lat)
        && lng < ((bLng - aLng) * (lat - aLat)) / (bLat - aLat) + aLng) {
      inside = !inside;
    }
  }
  return inside;
}

/** Sem polígono válido não há base geométrica para excluir o site. */
export function siteInsidePolygon(site, polygon) {
  if (!Array.isArray(polygon) || polygon.length < 3) return true;
  return pointInPolygon(site?.lat, site?.lng, polygon);
}
