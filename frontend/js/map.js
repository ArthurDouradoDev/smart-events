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
let _osmLayer = null;
let _satLayer = null;

const STATUS_COLORS = {
  healthy:  "#3FB950",
  warning:  "#D29922",
  critical: "#F85149",
  unknown:  "#484F58",
};

const FREQUENCY_RADII = {
  "3G": {
    "850": 11,
    "2100": 10
  },
  "4G": {
    "700": 21,
    "850": 20,
    "1800": 19,
    "2100": 18,
    "2300": 17,
    "2600": 16
  },
  "5G": {
    "700": 29,
    "2100": 28,
    "2300": 27,
    "3500": 26
  }
};

const FREQUENCY_COLORS = {
  "3G": {
    "850": "#ca4216",
    "2100": "#5496c6"
  },
  "4G": {
    "700": "#00ff9a",
    "850": "#ADFF2F",
    "1800": "#cb009a",
    "2100": "#191A42",
    "2300": "#FFAA00",
    "2600": "#ffff00"
  },
  "5G": {
    "700": "#460c0a",
    "2100": "#e347bf",
    "2300": "#09f008",
    "3500": "#1714dd"
  }
};

// ── Inicialização ─────────────────────────────────────────────────

export function initMap() {
  _map = L.map("map", {
    center: [-23.55, -46.63],
    zoom: 13,
    zoomControl: false,  // recriado no canto inferior direito (evita o drawer de alarmes à esquerda)
  });
  L.control.zoom({ position: "bottomright" }).addTo(_map);

  _osmLayer = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "",
  });

  _satLayer = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 19,
    attribution: "",
  });

  // Default: Claro
  _osmLayer.addTo(_map);
  const mapEl = document.getElementById("map");
  if (mapEl) {
    mapEl.classList.remove("dark-map");
  }

  // Basemap Selector
  const basemapSelector = document.getElementById("basemap-selector");
  if (basemapSelector) {
    basemapSelector.value = "light";
    basemapSelector.addEventListener("change", (e) => {
      setBasemap(e.target.value);
    });
  }

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
  State.on("change:vips", () => renderSites(State.sites));
  State.on("change:selectedSite", _onSiteSelected);
  State.on("change:techFilter", () => renderSites(State.sites || []));

  _map.on("zoomend", () => {
    Object.keys(_markers).forEach(id => {
      const site = State.sites.find(s => s.id === id);
      if (site) {
        _updateMarker(site);
      }
    });
  });
}

export function setBasemap(type) {
  if (!_map) return;
  const mapEl = document.getElementById("map");

  if (type === "dark") {
    if (!_map.hasLayer(_osmLayer)) {
      _map.removeLayer(_satLayer);
      _osmLayer.addTo(_map);
    }
    if (mapEl) mapEl.classList.add("dark-map");
  } else if (type === "light") {
    if (!_map.hasLayer(_osmLayer)) {
      _map.removeLayer(_satLayer);
      _osmLayer.addTo(_map);
    }
    if (mapEl) mapEl.classList.remove("dark-map");
  } else if (type === "satellite") {
    if (!_map.hasLayer(_satLayer)) {
      _map.removeLayer(_osmLayer);
      _satLayer.addTo(_map);
    }
    if (mapEl) mapEl.classList.remove("dark-map");
  }
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
  _map.invalidateSize();
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

  marker.bindPopup(_buildPopup(site), { className: "site-popup", autoPan: false });
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

function _resolveTechAndFreq(cell) {
  let tech = cell.tech;
  let freq = cell.frequency;

  if (!tech || !freq) {
    const id = (cell.id || "").toUpperCase();
    
    if (id.includes("5G") || id.includes("_5G") || id.startsWith("5G") || id.includes("-Y") || id.includes("_Y")) {
      tech = "5G";
    } else if (id.includes("4G") || id.includes("LTE") || id.includes("_4G") || id.startsWith("4G") || id.includes("-L") || id.includes("_L")) {
      tech = "4G";
    } else if (id.includes("3G") || id.includes("WCDMA") || id.includes("_3G") || id.startsWith("3G") || id.includes("-W") || id.includes("_W")) {
      tech = "3G";
    } else if (id.includes("2G") || id.includes("GSM") || id.includes("_2G") || id.startsWith("2G") || id.includes("-G") || id.includes("_G")) {
      tech = "2G";
    }

    const freqMatch = id.match(/(3500|2600|2300|2100|1800|850|700)/);
    if (freqMatch) {
      freq = freqMatch[1];
    } else {
      // Tenta mapear os shortcodes de frequência
      const tokens = id.split(/[-_]/);
      const shortcodeMap = {
        "35": "3500",
        "26": "2600",
        "23": "2300",
        "21": "2100",
        "18": "1800",
        "85": "850",
        "08": "850",
        "07": "700",
        "7": "700"
      };
      for (const token of tokens) {
        if (shortcodeMap[token]) {
          freq = shortcodeMap[token];
          break;
        }
        const suffixMatch = token.match(/^(35|26|23|21|18|85|08|07|7)([A-Z])$/);
        if (suffixMatch && shortcodeMap[suffixMatch[1]]) {
          freq = shortcodeMap[suffixMatch[1]];
          break;
        }
      }
    }

    if (!tech && freq) {
      if (freq === "3500") tech = "5G";
      else if (["700", "1800", "2300", "2600"].includes(freq)) tech = "4G";
      else if (freq === "850") tech = "4G";
      else if (freq === "2100") tech = "4G";
    }
  }
  return { tech, freq };
}

function _getZoomScale(zoom) {
  if (zoom >= 13) {
    return Math.min(2.0, 1.0 + (zoom - 13) * 0.2);
  } else {
    return Math.max(0.08, Math.pow(2, zoom - 13));
  }
}

function _getCellRadius(cell, zoom) {
  const { tech, freq } = _resolveTechAndFreq(cell);
  let baseRadius = 22;

  if (tech && freq && FREQUENCY_RADII[tech] && FREQUENCY_RADII[tech][freq]) {
    baseRadius = FREQUENCY_RADII[tech][freq];
  }

  const scale = _getZoomScale(zoom);
  return Math.max(3, baseRadius * scale);
}

function _getCellColor(cell) {
  const { tech, freq } = _resolveTechAndFreq(cell);

  if (tech && freq && FREQUENCY_COLORS[tech] && FREQUENCY_COLORS[tech][freq]) {
    return FREQUENCY_COLORS[tech][freq];
  }

  // Cores de fallback genéricas para cada tecnologia
  if (tech === "5G") return "#1714dd";
  if (tech === "4G") return "#ffff00";
  if (tech === "3G") return "#5496c6";
  if (tech === "2G") return "#8B949E";

  return "#484F58";
}

function _cellFamily(cell) {
  if (cell?.family) return cell.family;
  const id = String(cell?.id || cell?.tech || "").toUpperCase();
  const has4g = /(^|[^A-Z0-9])(?:4G|LTE)([^A-Z0-9]|$)/.test(id);
  const has5g = /(^|[^A-Z0-9])(?:5G|NR|NCI)([^A-Z0-9]|$)/.test(id);
  if (has4g === has5g) return null;
  return has4g ? "4G" : "5G";
}

function _visibleCells(site) {
  const cells = site.cells || [
    { azimuth: 0 }, { azimuth: 120 }, { azimuth: 240 }
  ];
  const filter = State.techFilter;
  if (!filter || filter === "all") return cells;
  return cells.filter(cell => {
    const family = _cellFamily(cell);
    return !family || family === filter;
  });
}

function _buildSectorIcon(site) {
  const visible = _visibleCells(site);
  const cells = visible.length
    ? visible
    : (site.cells || [{ azimuth: 0 }, { azimuth: 120 }, { azimuth: 240 }]);

  const zoom = _map ? _map.getZoom() : 13;
  const scale = _getZoomScale(zoom);

  cells.forEach(cell => {
    cell._radius = _getCellRadius(cell, zoom);
  });

  const maxR = Math.max(...cells.map(c => c._radius || 22), 22);
  const sweep = 100; // graus de abertura de cada setor

  // Se o site estiver selecionado ou possuir VIPs, aumenta o tamanho do SVG para acomodar o contorno com folga
  const isSelected = site.id === State.selectedSite;
  const hasVip = (State.vips || []).some(v => v.in_event && v.serving_site === site.id);
  const highlightDist = (isSelected ? Math.max(4, 7 * scale) : 2) + (hasVip ? Math.max(6, 10 * scale) : 0);
  const size = (maxR + highlightDist) * 2;
  const cx = size / 2;
  const cy = size / 2;

  const sortedCells = [...cells].sort((a, b) => (b._radius || 22) - (a._radius || 22));

  const strokeWidth = scale < 0.3 ? 0.3 : 1;
  const centerStrokeWidth = scale < 0.3 ? 0.5 : 1.5;

  const paths = sortedCells.map(cell => {
    const color = _getCellColor(cell);
    const az = cell.azimuth ?? 0;
    const r = cell._radius || 22;
    return `<path d="${_sectorPath(cx, cy, r, az, sweep)}"
                  fill="${color}" opacity="0.88" stroke="#0D1117" stroke-width="${strokeWidth}"/>`;
  });

  const siteColor = STATUS_COLORS[site.status] || STATUS_COLORS.unknown;
  const circleRadius = Math.max(1.2, 4.5 * scale);

  // Desenha um contorno/anel externo tracejado com uma distância das pétalas
  let highlightRing = "";
  if (isSelected) {
    const ringRadius = maxR + Math.max(1.5, 3.5 * scale);
    const ringStroke = Math.max(1, 2 * scale);
    highlightRing = `<circle cx="${cx}" cy="${cy}" r="${ringRadius}"
                             fill="none" stroke="var(--accent)" stroke-width="${ringStroke}"
                             stroke-dasharray="${4 * scale} ${3 * scale}" opacity="0.95"/>`;
  }

  // Desenha um badge dourado "V" no canto superior direito para sites com VIP conectado
  let vipBadge = "";
  if (hasVip) {
    const badgeRadius = Math.max(3.5, 7 * scale);
    const badgeX = cx + Math.max(7, 11 * scale);
    const badgeY = cy - Math.max(7, 11 * scale);
    const fontSize = Math.max(5, 9 * scale);
    vipBadge = `
      <circle cx="${badgeX}" cy="${badgeY}" r="${badgeRadius}" fill="var(--vip-gold)" stroke="#0D1117" stroke-width="${strokeWidth}"/>
      <text x="${badgeX}" y="${badgeY + 0.35 * badgeRadius}" font-size="${fontSize}" font-family="Outfit, sans-serif" font-weight="700" fill="#0D1117" text-anchor="middle">V</text>
    `;
  }

  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg"
         width="${size}" height="${size}"
         viewBox="0 0 ${size} ${size}">
      ${highlightRing}
      ${paths.join("")}
      <circle cx="${cx}" cy="${cy}" r="${circleRadius}"
              fill="${siteColor}" stroke="#0D1117" stroke-width="${centerStrokeWidth}"/>
      ${vipBadge}
    </svg>`;

  return L.divIcon({
    html: svg,
    className: "",
    iconSize: [size, size],
    iconAnchor: [cx, cy],
    popupAnchor: [0, -(cy + 4)],
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
  const utilization = Number(site.utilization);
  const util = site.utilization != null && Number.isFinite(utilization)
    ? `${utilization.toFixed(2)}%`
    : "—";
  const vipsAtSite = (State.vips || []).filter(v => v.in_event && v.serving_site === site.id);
  let vipListHtml = "";
  if (vipsAtSite.length > 0) {
    const vipNames = vipsAtSite.map(v => v.name).join(", ");
    vipListHtml = `
      <div style="margin-top: 6px; border-top: 1px solid var(--border); padding-top: 6px; color: var(--vip-gold); font-size: 11px; font-weight: 600; display: flex; align-items: center; gap: 4px;">
        <span>👑</span> <span>VIPs: ${_esc(vipNames)}</span>
      </div>
    `;
  }
  return `
    <div style="font-family:'Outfit',sans-serif; min-width:140px; color:#E6EDF3;">
      <div style="font-weight:600;font-size:13px;margin-bottom:4px">${_esc(site.name)}</div>
      <div style="font-size:11px;color:#8B949E">Utilização: ${util}</div>
      <div style="font-size:11px;color:#8B949E">Células: ${(site.cells||[]).length}</div>
      ${vipListHtml}
    </div>`;
}

function _esc(str) {
  return String(str ?? "")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
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

let _prevSelectedSiteId = null;

function _onSiteSelected(siteId) {
  // Atualiza o ícone do site selecionado anteriormente para remover o destaque
  if (_prevSelectedSiteId && _prevSelectedSiteId !== siteId) {
    const prevSite = State.sites.find(s => s.id === _prevSelectedSiteId);
    if (prevSite && _markers[_prevSelectedSiteId]) {
      _updateMarker(prevSite);
    }
  }

  if (!siteId) {
    _prevSelectedSiteId = null;
    return;
  }

  // Atualiza o ícone do novo site selecionado para desenhar o contorno
  const site = State.sites.find(s => s.id === siteId);
  const marker = _markers[siteId];
  if (marker && site && _map) {
    _updateMarker(site);
    
    // Aproxima o mapa no site selecionado com zoom de pelo menos 16
    const latLng = marker.getLatLng();
    _map.setView(latLng, Math.max(16, _map.getZoom()), { animate: true });
    
    // Abre o popup do site
    marker.openPopup();
  }

  _prevSelectedSiteId = siteId;
}
