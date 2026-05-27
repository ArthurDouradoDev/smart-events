/**
 * map.js — Mapa Leaflet com marcadores de setor (fan/pétala).
 *
 * Cada site é renderizado como setores SVG radiados,
 * coloridos pelo status atual da célula.
 */

import State from "./state.js";

let _map = null;
let _markers = {};         // site_id → L.Marker
let _polygon = null;
let _showEventOnly = false;
let _showPolygon = true;

const STATUS_COLORS = {
  healthy:  "#3FB950",
  warning:  "#D29922",
  critical: "#F85149",
  unknown:  "#484F58",
};

// ── Inicialização ─────────────────────────────────────────────────

export function initMap() {
  _map = L.map("map", {
    center: [-23.55, -46.63],
    zoom: 13,
    zoomControl: true,
  });

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "",
  }).addTo(_map);

  // Controles de mapa
  document.getElementById("btn-polygon").addEventListener("click", () => {
    _showPolygon = !_showPolygon;
    document.getElementById("btn-polygon").classList.toggle("active", _showPolygon);
    _renderPolygon();
  });
  document.getElementById("btn-event-only").addEventListener("click", () => {
    _showEventOnly = !_showEventOnly;
    document.getElementById("btn-event-only").classList.toggle("active", _showEventOnly);
    _applyVisibility();
  });

  // Reage a mudanças de estado
  State.on("change:sites", renderSites);
}

// ── Renderização dos sites ────────────────────────────────────────

export function renderSites(sites) {
  // Remove marcadores antigos que não existem mais
  const siteIds = new Set(sites.map(s => s.id));
  Object.keys(_markers).forEach(id => {
    if (!siteIds.has(id)) {
      _markers[id].remove();
      delete _markers[id];
    }
  });

  sites.forEach(site => {
    if (_markers[site.id]) {
      _updateMarker(site);
    } else {
      _createMarker(site);
    }
  });

  _applyVisibility();
}

export function renderEventPolygon(polygon) {
  if (_polygon) _polygon.remove();
  if (!polygon?.length) return;

  _polygon = L.polygon(polygon, {
    color:     "#388BFD",
    weight:    1.5,
    dashArray: "8 5",
    fillColor: "#388BFD",
    fillOpacity: 0.06,
  }).addTo(_map);
}

export function fitToEvent(sites, polygon) {
  if (!_map) return;
  const points = [
    ...sites.map(s => [s.lat, s.lng]),
    ...(polygon || []),
  ];
  if (points.length) _map.fitBounds(L.latLngBounds(points), { padding: [40, 40] });
}

// ── Internos ──────────────────────────────────────────────────────

function _createMarker(site) {
  const icon = _buildSectorIcon(site);
  const marker = L.marker([site.lat, site.lng], {
    icon,
    title: site.name,
    interactive: true,
  }).addTo(_map);

  marker.bindPopup(_buildPopup(site), { className: "site-popup" });
  marker.on("click", () => {
    State.set("selectedSite", site.id);
    marker.openPopup();
  });

  _markers[site.id] = marker;
}

function _updateMarker(site) {
  const marker = _markers[site.id];
  marker.setIcon(_buildSectorIcon(site));
  marker.setPopupContent(_buildPopup(site));
}

function _buildSectorIcon(site) {
  const cells = site.cells || [
    { azimuth: 0 }, { azimuth: 120 }, { azimuth: 240 }
  ];
  const R = 22;
  const sweep = 60; // graus de abertura de cada setor
  const size = (R + 2) * 2;

  const paths = cells.map(cell => {
    const color = STATUS_COLORS[cell.status] || STATUS_COLORS[site.status] || STATUS_COLORS.unknown;
    const az = cell.azimuth ?? 0;
    return `<path d="${_sectorPath(size/2, size/2, R, az, sweep)}"
                  fill="${color}" opacity="0.88" stroke="#0D1117" stroke-width="1"/>`;
  });

  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg"
         width="${size}" height="${size}"
         viewBox="0 0 ${size} ${size}">
      ${paths.join("")}
      <circle cx="${size/2}" cy="${size/2}" r="3.5"
              fill="#0D1117" stroke="#30363D" stroke-width="1"/>
    </svg>`;

  return L.divIcon({
    html: svg,
    className: "",
    iconSize: [size, size],
    iconAnchor: [size/2, size/2],
    popupAnchor: [0, -(size/2 + 4)],
  });
}

/**
 * Gera path SVG de um setor (fan) dado:
 *   cx,cy = centro, r = raio, azimuth = direção em graus (0=Norte),
 *   sweep = abertura em graus
 */
function _sectorPath(cx, cy, r, azimuth, sweep) {
  // Converte para coordenadas SVG (y invertido, 0° = Norte)
  const startDeg = azimuth - sweep / 2 - 90;
  const endDeg   = azimuth + sweep / 2 - 90;
  const start = _polar(cx, cy, r, startDeg);
  const end   = _polar(cx, cy, r, endDeg);
  const large = sweep > 180 ? 1 : 0;
  return `M${cx},${cy} L${start.x},${start.y} A${r},${r} 0 ${large},1 ${end.x},${end.y} Z`;
}

function _polar(cx, cy, r, deg) {
  const rad = (deg * Math.PI) / 180;
  return { x: +(cx + r * Math.cos(rad)).toFixed(2), y: +(cy + r * Math.sin(rad)).toFixed(2) };
}

function _buildPopup(site) {
  const util = site.utilization != null ? `${site.utilization}%` : "—";
  return `
    <div style="font-family:'Segoe UI',sans-serif; min-width:140px">
      <div style="font-weight:600;font-size:13px;margin-bottom:4px">${site.name}</div>
      <div style="font-size:11px;color:#8B949E">Utilização: ${util}</div>
      <div style="font-size:11px;color:#8B949E">Células: ${(site.cells||[]).length}</div>
    </div>`;
}

function _renderPolygon() {
  if (!_polygon) return;
  if (_showPolygon) _polygon.addTo(_map);
  else _polygon.remove();
}

function _applyVisibility() {
  Object.entries(_markers).forEach(([id, marker]) => {
    const site = State.sites.find(s => s.id === id);
    if (!site) return;
    const hide = _showEventOnly && !site.is_event_site;
    if (hide) marker.remove();
    else if (!_map.hasLayer(marker)) marker.addTo(_map);
  });
}
