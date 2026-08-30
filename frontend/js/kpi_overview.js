/**
 * kpi_overview.js — Visão geral full-screen com nove gráficos sincronizados.
 *
 * O escopo é uma comparação: três seletores múltiplos independentes (Clusters,
 * Sites e Células) alimentam uma única consulta multi-escopo. Cada escopo vira uma cor
 * estável; nos painéis pareados o DL é linha contínua e o UL é a mesma cor
 * tracejada, sobre um único eixo — os dois lados compartilham a unidade.
 */

import State from "./state.js";
import API from "./bridge.js";
import { escalaDaMetrica, esquecerEscalas, formatar } from "./units.js";

const PANELS = {
  "4G": [
    { id: "accessibility", title: "Acessibilidade", metrics: ["accessibility"] },
    { id: "availability", title: "Availability", metrics: ["availability"] },
    { id: "drop_rate", title: "Drop", metrics: ["drop_rate"] },
    { id: "utilization_dl", title: "DL PRB", metrics: ["utilization_dl"] },
    { id: "utilization_ul", title: "UL PRB", metrics: ["utilization_ul"] },
    { id: "interference_ul", title: "Interferência", metrics: ["interference_ul"] },
    { id: "throughput_dl", title: "Throughput DL", metrics: ["throughput_dl"] },
    { id: "throughput_ul", title: "Throughput UL", metrics: ["throughput_ul"] },
    { id: "user_count", title: "UE médio", metrics: ["user_count"] },
  ],
  "5G": [
    { id: "accessibility", title: "Acessibilidade", metrics: ["accessibility"] },
    { id: "drop_rate", title: "Drop", metrics: ["drop_rate"] },
    { id: "user_count", title: "User Médio", metrics: ["user_count"] },
    { id: "availability", title: "Availability", metrics: ["availability"] },
    { id: "interference_ul", title: "UL Interference", metrics: ["interference_ul"] },
    { id: "prb_utility", title: "PRB Utility", metrics: ["utilization_dl", "utilization_ul"], paired: true },
    { id: "throughput", title: "Throughput", metrics: ["throughput_dl", "throughput_ul"], paired: true },
    { id: "traffic_volume_sa", title: "Traffic Volume SA", metrics: ["traffic_volume_dl_sa", "traffic_volume_ul_sa"], paired: true },
    { id: "traffic_volume_nsa", title: "Traffic Volume NSA", metrics: ["traffic_volume_dl_nsa", "traffic_volume_ul_nsa"], paired: true },
  ],
};

/**
 * Paleta categórica do app reordenada para a superfície escura dos cards.
 * A ordem é o mecanismo de segurança para daltonismo (pares adjacentes com
 * ΔE CVD ≥ 8), não enfeite: não reordene sem revalidar. A cor segue a
 * entidade — cluster usa a cor cadastrada, site usa o índice estável dele na
 * lista do evento —, de modo que mudar a seleção nunca repinta quem ficou.
 */
const SERIES_COLORS = [
  "#388BFD", "#F85149", "#ab7df6", "#3FB950",
  "#00d2ff", "#D29922", "#f692cc", "#FF7B00",
];
const MAX_SCOPES = SERIES_COLORS.length;
const UL_DASH = [6, 4];

let _family = "4G";
let _minutes = 60;
let _requestId = 0;
let _activeIndex = null;
let _hoveredPanelId = null;
let _scopeSites = [];
let _scopeClusters = [];
let _scopeCells = [];
const _selection = { cluster: new Set(), site: new Set(), cell: new Set(), siteCarrier: new Set() };
const _hiddenScopes = new Set();
// Sites com a expansão "separar por portadora" aberta no seletor de sites.
const _expandedSites = new Set();
// Marca sites cuja expansão já selecionou as portadoras automaticamente uma
// vez nesta sessão da visão geral — reabrir depois de recolher não repete.
const _autoMarkedCarrierSites = new Set();
const _charts = new Map();
// Escala vigente de cada painel: o tooltip e os ticks precisam da mesma.
const _panelScales = new Map();
let _scopeMeta = new Map();
// A visão pode abrir antes de o app terminar de carregar sites/clusters; nesse
// caso a semente de seleção é reaplicada quando os dados chegam.
let _pendingSeed = false;

const _crosshairPlugin = {
  id: "overviewCrosshair",
  afterDatasetsDraw(chart) {
    if (_activeIndex == null || !chart.chartArea || !chart.scales?.x) return;
    const x = chart.scales.x.getPixelForValue(_activeIndex);
    if (!Number.isFinite(x)) return;
    const { top, bottom, left, right } = chart.chartArea;
    if (x < left || x > right) return;
    const ctx = chart.ctx;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(230, 237, 243, 0.72)";
    ctx.setLineDash([3, 3]);
    ctx.stroke();
    ctx.restore();
  },
};

export function initKpiOverview() {
  const modal = document.getElementById("kpi-overview-modal");
  const openButton = document.getElementById("open-kpi-overview");
  const closeButton = document.getElementById("kpi-overview-close");
  if (!modal || !openButton) return;

  openButton.addEventListener("click", _open);
  closeButton?.addEventListener("click", _close);
  modal.addEventListener("click", event => {
    if (event.target === modal) _close();
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && !modal.classList.contains("hidden")) {
      if (_closeOpenPicker()) return;
      _close();
    }
  });

  document.querySelectorAll("#kpi-overview-family-tabs [data-family]").forEach(button => {
    button.addEventListener("click", () => {
      if (_family === button.dataset.family) return;
      _family = button.dataset.family;
      _dropFamilySpecificSelection();
      _syncFamilyTabs();
      _renderPanelShells();
      _renderPickers();
      void _load();
      // A lista de células é específica de família: precisa ser buscada de novo.
      // Um `_refreshScopeData()` completo reaplicaria a semente de seleção
      // (herdada do dashboard) sempre que a comparação estiver vazia — o que
      // reintroduziria um escopo que o usuário acabou de limpar.
      void _refreshCellsForFamily();
    });
  });

  document.querySelectorAll("#kpi-overview-time-tabs [data-window]").forEach(button => {
    button.addEventListener("click", () => {
      _minutes = Number(button.dataset.window);
      _syncTimeTabs();
      void _load();
    });
  });

  _initPickers();

  State.on("change:sites", sites => {
    if (!_isOpen()) _scopeSites = sites || [];
  });
  State.on("change:clusters", clusters => {
    if (!_isOpen()) _scopeClusters = clusters || [];
  });
  // Trocar de evento troca a ordem de grandeza: a memória de escala (compartilhada
  // com o dashboard, é o mesmo módulo) não pode atravessar essa fronteira.
  State.on("change:activeEvent", () => {
    esquecerEscalas();
    if (_isOpen()) void _refreshScopeData();
  });
  State.on("change:historicalEvent", () => {
    esquecerEscalas();
    if (_isOpen()) void _refreshScopeData();
  });
}

function _isOpen() {
  return !document.getElementById("kpi-overview-modal")?.classList.contains("hidden");
}

function _open() {
  const modal = document.getElementById("kpi-overview-modal");
  if (!modal) return;
  document.getElementById("chart-popup-modal")?.classList.add("hidden");
  _minutes = [0, 15, 30, 60].includes(Number(State.timeWindow)) ? Number(State.timeWindow) : 60;
  _scopeSites = State.sites || [];
  _scopeClusters = State.clusters || [];
  _scopeCells = [];
  _family = _preferredFamily();
  _hiddenScopes.clear();
  _expandedSites.clear();
  _autoMarkedCarrierSites.clear();
  _pendingSeed = true;
  _applyPreferredSelection();
  modal.classList.remove("hidden");
  _syncFamilyTabs();
  _syncTimeTabs();
  _renderPickers();
  _renderPanelShells();
  void _load();
  void _refreshScopeData();
}

function _close() {
  document.getElementById("kpi-overview-modal")?.classList.add("hidden");
  _closeOpenPicker();
  _requestId += 1;
  _clearHover();
}

function _syncFamilyTabs() {
  document.querySelectorAll("#kpi-overview-family-tabs [data-family]").forEach(button => {
    button.classList.toggle("active", button.dataset.family === _family);
  });
}

function _syncTimeTabs() {
  document.querySelectorAll("#kpi-overview-time-tabs [data-window]").forEach(button => {
    button.classList.toggle("active", Number(button.dataset.window) === _minutes);
  });
}

// ── Seleção de escopos ─────────────────────────────────────────────

/** Clusters gerados automaticamente a partir do DLEARFCN, da família ativa. */
function _carrierClusters() {
  return _familyFilteredClusters().filter(cluster => cluster.source === "earfcn");
}

/**
 * Clusters visíveis na aba ativa: os de família diferente da aba ficam de fora
 * — senão "Todos os clusters" marcaria escopos que renderizariam vazios na
 * família errada. Vale tanto para a portadora (EARFCN) quanto para o cluster
 * manual recortado só de células de uma tecnologia; o de família indeterminada
 * (o caso normal do cluster de sites, que tem as duas) fica nas duas abas.
 */
function _familyFilteredClusters() {
  return _scopeClusters.filter(cluster =>
    !cluster.family || cluster.family === _family);
}

/**
 * Aba de abertura: a do dashboard, exceto quando o "ver KPIs" veio de um cluster
 * de uma tecnologia só — aí é a dele, senão o cluster pedido abriria na aba onde
 * não tem célula nenhuma e renderiza nove painéis vazios.
 */
function _preferredFamily() {
  const requested = typeof State.selectedSite === "string"
      && State.selectedSite.startsWith("cluster:")
    ? _scopeClusters.find(cluster =>
        cluster.id === State.selectedSite.slice("cluster:".length))
    : null;
  if (requested?.family) return requested.family;
  return State.techFilter === "5G" ? "5G" : "4G";
}

/** Herda o recorte que o usuário já tinha no dashboard ao abrir a visão geral. */
function _applyPreferredSelection() {
  _selection.cluster.clear();
  _selection.site.clear();
  _selection.cell.clear();
  _selection.siteCarrier.clear();
  // Só clusters da aba ativa: semear com um da outra família devolveria o escopo
  // invisível que a troca de aba acabou de purgar.
  const visibleClusters = _familyFilteredClusters();
  const clusterIds = new Set(visibleClusters.map(cluster => cluster.id));
  const siteIds = new Set(_scopeSites.map(site => site.id));
  const carriers = _carrierClusters();

  if (State.selectedSite === "clusters:compare") {
    visibleClusters.slice(0, MAX_SCOPES).forEach(cluster => _selection.cluster.add(cluster.id));
  } else if (typeof State.selectedSite === "string" && State.selectedSite.startsWith("cluster:")) {
    const id = State.selectedSite.slice("cluster:".length);
    if (clusterIds.has(id)) _selection.cluster.add(id);
  } else if (carriers.length) {
    // Portadoras (EARFCN) são o recorte padrão em qualquer família, à frente
    // do site que o dashboard sempre deixa selecionado na lista.
    carriers.slice(0, MAX_SCOPES).forEach(cluster => _selection.cluster.add(cluster.id));
  } else if (siteIds.has(State.selectedSite)) {
    _selection.site.add(State.selectedSite);
  }

  if (!_selectionSize() && clusterIds.has(State.clusterFilter)) {
    _selection.cluster.add(State.clusterFilter);
  }
  if (!_selectionSize()) {
    if (visibleClusters[0]) _selection.cluster.add(visibleClusters[0].id);
    else if (_scopeSites[0]) _selection.site.add(_scopeSites[0].id);
  }
}

/**
 * Purga da comparação, ao trocar de aba, tudo que é específico de tecnologia.
 *
 * Atravessam a troca só os escopos que existem nas duas famílias: cluster sem
 * família definida (o cluster de sites) e site. Portadora — cluster EARFCN e
 * site×portadora —, cluster de uma família só e célula pertencem a uma
 * tecnologia; os seletores já os escondem na outra aba, e sem a purga eles
 * sobreviveriam como escopo invisível: viram chip, entram na consulta,
 * renderizam painel vazio e não há linha na lista para desmarcá-los.
 *
 * Se a purga zerar uma comparação que tinha algo, a semente do dashboard é
 * reaplicada para a família nova — abrir a aba no estado de erro "selecione ao
 * menos um" lê como painel quebrado. Comparação que já estava vazia continua
 * vazia: aí o vazio foi escolha do usuário.
 */
function _dropFamilySpecificSelection() {
  const had = _selectionSize() > 0;
  const visibleClusters = new Set(_familyFilteredClusters().map(cluster => cluster.id));
  [..._selection.cluster].forEach(id => {
    if (!visibleClusters.has(id)) _selection.cluster.delete(id);
  });
  const visibleCarriers = new Set();
  _scopeSites.forEach(site => (site.carriers || []).forEach(carrier => {
    if (!carrier.family || carrier.family === _family) {
      visibleCarriers.add(`${site.id}::${carrier.earfcn}`);
    }
  }));
  [..._selection.siteCarrier].forEach(key => {
    if (!visibleCarriers.has(key)) _selection.siteCarrier.delete(key);
  });
  // Célula é sempre de uma família só: nenhuma da aba anterior sobrevive.
  _selection.cell.clear();
  _hiddenScopes.clear();
  _expandedSites.clear();
  _autoMarkedCarrierSites.clear();
  if (had && !_selectionSize()) _applyPreferredSelection();
}

function _plural(count, singular) {
  return `${count} ${singular}${count === 1 ? "" : "s"}`;
}

function _selectionSize() {
  return _selection.cluster.size + _selection.site.size + _selection.cell.size
    + _selection.siteCarrier.size;
}

/** Assinatura estável da seleção — usada para decidir se vale recarregar. */
function _selectionKey() {
  return _selectedScopes().map(scope => scope.key).join("|");
}

/** Cor-base do site: índice estável dele na lista do evento, não na seleção. */
function _siteBaseColors() {
  const ordered = _scopeSites.map(site => String(site.id)).sort();
  return new Map(ordered.map((id, index) => [
    id, SERIES_COLORS[index % SERIES_COLORS.length],
  ]));
}

function _clusterColor(cluster, index) {
  return cluster.color || SERIES_COLORS[index % SERIES_COLORS.length];
}

/** Cor da portadora cadastrada (`earfcn-<v>`) por valor de EARFCN — a mesma
 * cor que `Portadora <v>` usa no cluster global, para o site×portadora sair
 * pareado com ela quando ela está na comparação. */
function _carrierClusterColorMap() {
  const map = new Map();
  _scopeClusters.forEach((cluster, index) => {
    if (cluster.source === "earfcn") map.set(cluster.id, _clusterColor(cluster, index));
  });
  return map;
}

function _siteCarrierName(siteId, earfcn) {
  const site = _scopeSites.find(candidate => String(candidate.id) === String(siteId));
  return `${site?.name || siteId} · ${earfcn}`;
}

/** `key` no formato `<site_id>::<earfcn>` — earfcn é sempre o último segmento. */
function _siteCarrierLabel(key) {
  const index = key.lastIndexOf("::");
  if (index === -1) return key;
  return _siteCarrierName(key.slice(0, index), key.slice(index + 2));
}

/** Cor-base da célula: mesma ideia do site, índice estável na lista do evento. */
function _cellBaseColors() {
  const ordered = _scopeCells.map(cell => String(cell.id)).sort();
  return new Map(ordered.map((id, index) => [
    id, SERIES_COLORS[index % SERIES_COLORS.length],
  ]));
}

/**
 * Escopos selecionados, na ordem de exibição: clusters, depois sites, depois
 * células.
 *
 * O cluster nunca muda de cor — é a cor cadastrada dele, a mesma do polígono no
 * mapa. Site e célula têm cor-base estável (índice deles na própria lista) e só
 * cedem o lugar quando ela já foi tomada: duas séries da mesma cor no mesmo
 * gráfico seriam ilegíveis, e são site/célula que não têm cor própria a defender.
 */
function _selectedScopes() {
  const scopes = [];
  const taken = new Set();
  _scopeClusters.forEach((cluster, index) => {
    if (!_selection.cluster.has(cluster.id)) return;
    const color = _clusterColor(cluster, index);
    taken.add(color.toLowerCase());
    scopes.push({
      key: `cluster:${cluster.id}`,
      scope: "cluster",
      scopeId: cluster.id,
      name: cluster.name || cluster.id,
      color,
    });
  });
  const baseColors = _siteBaseColors();
  _scopeSites.forEach(site => {
    if (!_selection.site.has(site.id)) return;
    const base = baseColors.get(String(site.id)) || SERIES_COLORS[0];
    const color = taken.has(base.toLowerCase())
      ? (SERIES_COLORS.find(candidate => !taken.has(candidate.toLowerCase())) || base)
      : base;
    taken.add(color.toLowerCase());
    scopes.push({
      key: `site:${site.id}`,
      scope: "site",
      scopeId: site.id,
      name: site.name || site.id,
      color,
    });
  });
  const carrierColors = _carrierClusterColorMap();
  _scopeSites.forEach(site => {
    (site.carriers || []).forEach(carrier => {
      const key = `${site.id}::${carrier.earfcn}`;
      if (!_selection.siteCarrier.has(key)) return;
      const base = carrierColors.get(`earfcn-${carrier.earfcn}`) || SERIES_COLORS[0];
      const color = taken.has(base.toLowerCase())
        ? (SERIES_COLORS.find(candidate => !taken.has(candidate.toLowerCase())) || base)
        : base;
      taken.add(color.toLowerCase());
      scopes.push({
        key: `site_carrier:${key}`,
        scope: "site_carrier",
        scopeId: key,
        name: _siteCarrierName(site.id, carrier.earfcn),
        color,
      });
    });
  });
  const cellBaseColors = _cellBaseColors();
  _scopeCells.forEach(cell => {
    if (!_selection.cell.has(cell.id)) return;
    const base = cellBaseColors.get(String(cell.id)) || SERIES_COLORS[0];
    const color = taken.has(base.toLowerCase())
      ? (SERIES_COLORS.find(candidate => !taken.has(candidate.toLowerCase())) || base)
      : base;
    taken.add(color.toLowerCase());
    scopes.push({
      key: `cell:${cell.id}`,
      scope: "cell",
      scopeId: cell.id,
      name: cell.name || cell.id,
      color,
    });
  });
  return scopes;
}

function _initPickers() {
  document.querySelectorAll("#kpi-overview-modal .scope-picker").forEach(picker => {
    picker.querySelector(".scope-picker-trigger")?.addEventListener("click", event => {
      event.stopPropagation();
      const willOpen = !picker.classList.contains("open");
      _closeOpenPicker();
      if (willOpen) _openPicker(picker);
    });
    picker.querySelector(".scope-picker-menu")?.addEventListener("click", event => {
      event.stopPropagation();
    });
  });
  document.getElementById("kpi-overview-modal")?.addEventListener("click", () => _closeOpenPicker());
}

function _openPicker(picker) {
  picker.classList.add("open");
  picker.querySelector(".scope-picker-trigger")?.setAttribute("aria-expanded", "true");
  picker.querySelector(".scope-picker-menu")?.classList.remove("hidden");
  picker.querySelector(".scope-picker-search")?.focus();
}

function _closeOpenPicker() {
  const open = document.querySelector("#kpi-overview-modal .scope-picker.open");
  if (!open) return false;
  open.classList.remove("open");
  open.querySelector(".scope-picker-trigger")?.setAttribute("aria-expanded", "false");
  open.querySelector(".scope-picker-menu")?.classList.add("hidden");
  return true;
}

function _renderPickers() {
  _renderClusterPicker();
  _renderSitePicker();
  _renderCellPicker();
  _syncPickerSummaries();
}

function _optionRow({ checked, blocked, color, name, meta, onToggle }) {
  const label = document.createElement("label");
  label.className = `scope-picker-option${blocked ? " is-blocked" : ""}`;
  label.setAttribute("role", "option");
  label.setAttribute("aria-selected", String(checked));
  label.innerHTML = `
    <input type="checkbox" ${checked ? "checked" : ""} ${blocked ? "disabled" : ""}>
    ${color ? `<span class="scope-picker-swatch" style="background:${_safeColor(color)}"></span>` : ""}
    <span class="scope-picker-option-name">${_esc(name)}</span>
    ${meta ? `<span class="scope-picker-option-meta">${_esc(meta)}</span>` : ""}`;
  label.querySelector("input").addEventListener("change", event => onToggle(event.target.checked));
  return label;
}

function _renderClusterPicker() {
  const menu = document.querySelector("#kpi-overview-cluster-picker .scope-picker-menu");
  if (!menu) return;
  menu.innerHTML = "";
  const clusters = _familyFilteredClusters();
  if (!clusters.length) {
    menu.innerHTML = `<div class="scope-picker-empty">Nenhum cluster cadastrado neste evento.</div>`;
    return;
  }

  const options = document.createElement("div");
  options.className = "scope-picker-options";
  const allSelected = clusters.every(cluster => _selection.cluster.has(cluster.id));
  const fitsAll = clusters.length + _selection.site.size + _selection.cell.size
    + _selection.siteCarrier.size <= MAX_SCOPES;
  options.appendChild(_optionRow({
    checked: allSelected,
    blocked: !allSelected && !fitsAll,
    name: "Todos os clusters",
    meta: _plural(clusters.length, "cluster"),
    onToggle: checked => {
      _selection.cluster.clear();
      if (checked) clusters.forEach(cluster => _selection.cluster.add(cluster.id));
      _onSelectionChanged();
    },
  }));
  const divider = document.createElement("div");
  divider.className = "scope-picker-divider";
  options.appendChild(divider);

  clusters.forEach((cluster, index) => {
    const checked = _selection.cluster.has(cluster.id);
    options.appendChild(_optionRow({
      checked,
      blocked: !checked && _selectionSize() >= MAX_SCOPES,
      color: _clusterColor(cluster, index),
      name: cluster.name || cluster.id,
      meta: _plural(cluster.site_count, "site"),
      onToggle: enabled => {
        if (enabled) _selection.cluster.add(cluster.id);
        else _selection.cluster.delete(cluster.id);
        _onSelectionChanged();
      },
    }));
  });
  menu.appendChild(options);
}

/** Monta a casca do seletor de sites uma vez; a busca só repinta as opções. */
function _renderSitePicker() {
  const menu = document.querySelector("#kpi-overview-site-picker .scope-picker-menu");
  if (!menu) return;
  menu.innerHTML = "";

  const search = document.createElement("input");
  search.type = "search";
  search.className = "scope-picker-search";
  search.placeholder = "Buscar site…";
  search.addEventListener("input", () => _renderSiteOptions());
  menu.appendChild(search);

  const options = document.createElement("div");
  options.className = "scope-picker-options";
  menu.appendChild(options);
  _renderSiteOptions();
}

/**
 * Meta da linha de site no seletor: famílias + quantas portadoras o site tem
 * na aba ativa. Quais são elas fica para as sub-linhas da expansão — enumerar
 * os EARFCNs aqui empurrava o nome do site para fora da linha.
 */
function _siteOptionMeta(site, carriers) {
  const parts = [(site.tech_families || []).join("/")];
  if (carriers.length) parts.push(_plural(carriers.length, "portadora"));
  return parts.filter(Boolean).join(" · ");
}

function _renderSiteOptions() {
  const menu = document.querySelector("#kpi-overview-site-picker .scope-picker-menu");
  const options = menu?.querySelector(".scope-picker-options");
  if (!options) return;
  const term = (menu.querySelector(".scope-picker-search")?.value || "").trim().toLowerCase();
  const matches = _scopeSites.filter(site =>
    !term || `${site.name || ""} ${site.original_name || ""} ${site.id}`
      .toLowerCase().includes(term));

  options.innerHTML = "";
  if (!matches.length) {
    const empty = document.createElement("div");
    empty.className = "scope-picker-empty";
    empty.textContent = _scopeSites.length ? "Nenhum site encontrado." : "Nenhum site no evento.";
    options.appendChild(empty);
    return;
  }
  const baseColors = _siteBaseColors();
  const carrierColors = _carrierClusterColorMap();
  matches.forEach(site => {
    const checked = _selection.site.has(site.id);
    const carriers = (site.carriers || []).filter(carrier =>
      !carrier.family || carrier.family === _family);
    const row = _optionRow({
      checked,
      blocked: !checked && _selectionSize() >= MAX_SCOPES,
      color: baseColors.get(String(site.id)),
      name: site.name || site.id,
      meta: _siteOptionMeta(site, carriers),
      onToggle: enabled => {
        if (enabled) _selection.site.add(site.id);
        else _selection.site.delete(site.id);
        _onSelectionChanged();
      },
    });

    // Basta uma portadora para o site ganhar o chevron: com uma só a expansão
    // não separa nada, mas é o que informa ao usuário que o recorte por
    // portadora existe — sem isso a funcionalidade fica invisível nos eventos
    // com uma portadora por família.
    if (!carriers.length) {
      options.appendChild(row);
      return;
    }

    const expanded = _expandedSites.has(site.id);
    const chevron = document.createElement("button");
    chevron.type = "button";
    chevron.className = `scope-picker-chevron${expanded ? " is-open" : ""}`;
    chevron.title = expanded ? "Recolher portadoras"
      : (carriers.length > 1 ? "Separar por portadora" : "Ver a portadora do site");
    chevron.setAttribute("aria-expanded", String(expanded));
    chevron.textContent = "›";
    chevron.addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
      _toggleSiteCarrierExpansion(site, carriers);
    });
    row.appendChild(chevron);
    options.appendChild(row);

    if (!expanded) return;
    carriers.forEach(carrier => {
      const key = `${site.id}::${carrier.earfcn}`;
      const carrierChecked = _selection.siteCarrier.has(key);
      const subRow = _optionRow({
        checked: carrierChecked,
        blocked: !carrierChecked && _selectionSize() >= MAX_SCOPES,
        color: carrierColors.get(`earfcn-${carrier.earfcn}`),
        name: `Portadora ${carrier.earfcn}`,
        meta: _plural(carrier.cell_count, "célula"),
        onToggle: enabled => {
          if (enabled) _selection.siteCarrier.add(key);
          else _selection.siteCarrier.delete(key);
          _onSelectionChanged();
        },
      });
      subRow.classList.add("scope-picker-suboption");
      options.appendChild(subRow);
    });
  });
}

/**
 * Chevron "separar por portadora": alterna a expansão do site no seletor.
 * Na primeira vez que um site é expandido nesta sessão, marca todas as
 * portadoras dele de uma vez — expandir tem que simplesmente funcionar, e não
 * bater no teto de escopos em silêncio, então a seleção anterior é limpa
 * quando as portadoras não cabem sozinhas.
 */
function _toggleSiteCarrierExpansion(site, carriers) {
  if (_expandedSites.has(site.id)) {
    _expandedSites.delete(site.id);
    _renderSiteOptions();
    _syncPickerSummaries();
    return;
  }
  _expandedSites.add(site.id);
  if (!_autoMarkedCarrierSites.has(site.id)) {
    _autoMarkedCarrierSites.add(site.id);
    const keys = carriers.map(carrier => `${site.id}::${carrier.earfcn}`);
    const newKeys = keys.filter(key => !_selection.siteCarrier.has(key));
    if (_selectionSize() + newKeys.length > MAX_SCOPES) {
      _selection.cluster.clear();
      _selection.site.clear();
      _selection.cell.clear();
      _selection.siteCarrier.clear();
    }
    keys.forEach(key => _selection.siteCarrier.add(key));
  }
  _onSelectionChanged();
}

/** Monta a casca do seletor de células uma vez; a busca só repinta as opções. */
function _renderCellPicker() {
  const menu = document.querySelector("#kpi-overview-cell-picker .scope-picker-menu");
  if (!menu) return;
  menu.innerHTML = "";

  const search = document.createElement("input");
  search.type = "search";
  search.className = "scope-picker-search";
  search.placeholder = "Buscar célula…";
  search.addEventListener("input", () => _renderCellOptions());
  menu.appendChild(search);

  const options = document.createElement("div");
  options.className = "scope-picker-options";
  menu.appendChild(options);
  _renderCellOptions();
}

/**
 * Ids dos sites que balizam a lista de células oferecida no seletor.
 *
 * Com cluster e/ou site selecionados na comparação, a lista se restringe às
 * células desses sites — inclui o site direto e todo site que toca algum dos
 * clusters escolhidos (mesma granularidade de `site.cluster_ids` que o filtro
 * de cluster do dashboard já usa; não desce a seleção parcial de célula do
 * cluster). Sem nada selecionado, cai para as células de qualquer cluster do
 * evento — é o recorte "o que já está organizado", não o evento inteiro.
 */
function _cellScopeSiteIds() {
  const ids = new Set();
  if (_selection.site.size || _selection.cluster.size || _selection.siteCarrier.size) {
    _selection.site.forEach(id => ids.add(id));
    _selection.siteCarrier.forEach(key => {
      const index = key.lastIndexOf("::");
      if (index !== -1) ids.add(key.slice(0, index));
    });
    _scopeSites.forEach(site => {
      if ((site.cluster_ids || []).some(id => _selection.cluster.has(id))) ids.add(site.id);
    });
  } else {
    _scopeSites.forEach(site => {
      if ((site.cluster_ids || []).length) ids.add(site.id);
    });
  }
  return ids;
}

function _renderCellOptions() {
  const menu = document.querySelector("#kpi-overview-cell-picker .scope-picker-menu");
  const options = menu?.querySelector(".scope-picker-options");
  if (!options) return;
  const term = (menu.querySelector(".scope-picker-search")?.value || "").trim().toLowerCase();
  const scopeSiteIds = _cellScopeSiteIds();
  // Célula já escolhida continua oferecida mesmo se o site dela sair do
  // recorte depois — senão o usuário perderia o único jeito de desmarcá-la.
  const pool = _scopeCells.filter(cell =>
    scopeSiteIds.has(cell.site_id) || _selection.cell.has(cell.id));
  const matches = pool.filter(cell =>
    !term || `${cell.name || ""} ${cell.id} ${cell.site_name || ""}`.toLowerCase().includes(term));

  options.innerHTML = "";
  if (!matches.length) {
    const empty = document.createElement("div");
    empty.className = "scope-picker-empty";
    empty.textContent = !scopeSiteIds.size
      ? "Nenhum cluster cadastrado nem site selecionado."
      : term
        ? "Nenhuma célula encontrada."
        : "Nenhuma célula nesta tecnologia.";
    options.appendChild(empty);
    return;
  }
  const baseColors = _cellBaseColors();
  matches.forEach(cell => {
    const checked = _selection.cell.has(cell.id);
    options.appendChild(_optionRow({
      checked,
      blocked: !checked && _selectionSize() >= MAX_SCOPES,
      color: baseColors.get(String(cell.id)),
      name: cell.name || cell.id,
      meta: cell.site_name || "",
      onToggle: enabled => {
        if (enabled) _selection.cell.add(cell.id);
        else _selection.cell.delete(cell.id);
        _onSelectionChanged();
      },
    }));
  });
}

function _onSelectionChanged() {
  _pendingSeed = false;
  _hiddenScopes.clear();
  _renderClusterPicker();
  _renderSiteOptions();
  _renderCellOptions();
  _syncPickerSummaries();
  void _load();
}

function _summaryText(kind, entries) {
  const selected = entries.filter(entry => _selection[kind].has(entry.id));
  if (!entries.length) return kind === "cluster" ? "Nenhum cadastrado" : "Nenhum no evento";
  if (!selected.length) return "Nenhum";
  if (kind === "cluster" && selected.length === entries.length && entries.length > 1) return "Todos";
  if (selected.length === 1) return selected[0].name || selected[0].id;
  return `${selected.length} selecionados`;
}

/** Resumo do seletor de sites: conta sites e portadoras separadamente. */
function _siteSummaryText() {
  if (!_scopeSites.length) return "Nenhum no evento";
  const siteCount = _selection.site.size;
  const carrierCount = _selection.siteCarrier.size;
  if (!siteCount && !carrierCount) return "Nenhum";
  if (siteCount && carrierCount) {
    return `${_plural(siteCount, "site")} · ${_plural(carrierCount, "portadora")}`;
  }
  if (siteCount) {
    if (siteCount > 1) return `${siteCount} selecionados`;
    const site = _scopeSites.find(candidate => _selection.site.has(candidate.id));
    return site?.name || site?.id || "1 selecionado";
  }
  if (carrierCount > 1) return _plural(carrierCount, "portadora");
  return _siteCarrierLabel([..._selection.siteCarrier][0]);
}

function _syncPickerSummaries() {
  const clusterValue = document.querySelector("#kpi-overview-cluster-picker .scope-picker-value");
  const siteValue = document.querySelector("#kpi-overview-site-picker .scope-picker-value");
  const cellValue = document.querySelector("#kpi-overview-cell-picker .scope-picker-value");
  if (clusterValue) clusterValue.textContent = _summaryText("cluster", _scopeClusters);
  if (siteValue) siteValue.textContent = _siteSummaryText();
  if (cellValue) cellValue.textContent = _summaryText("cell", _scopeCells);

  const hint = document.getElementById("kpi-overview-scope-hint");
  if (hint) {
    hint.textContent = _selectionSize() >= MAX_SCOPES
      ? `Limite de ${MAX_SCOPES} escopos atingido`
      : "";
  }
  _syncContextLabel();
}

/**
 * Legenda única no cabeçalho, em vez de nove legendas iguais espremidas dentro
 * dos cards. Clicar num escopo o oculta nos nove painéis de uma vez.
 */
function _syncContextLabel() {
  const context = document.getElementById("kpi-overview-context");
  if (!context) return;
  const scopes = _selectedScopes();
  context.innerHTML = "";
  if (!scopes.length) {
    context.textContent = "Nenhum escopo selecionado";
    return;
  }
  scopes.forEach(scope => {
    const hidden = _hiddenScopes.has(scope.key);
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `kpi-overview-chip${hidden ? " is-off" : ""}`;
    chip.dataset.scopeKey = scope.key;
    chip.setAttribute("aria-pressed", String(!hidden));
    chip.title = hidden ? `Mostrar ${scope.name}` : `Ocultar ${scope.name}`;
    chip.innerHTML = `<i style="background:${_safeColor(scope.color)}"></i>${_esc(scope.name)}`;
    chip.addEventListener("click", () => _toggleScopeVisibility(scope.key));
    context.appendChild(chip);
  });
}

function _toggleScopeVisibility(scopeKey) {
  const scopes = _selectedScopes();
  const visible = scopes.filter(scope => !_hiddenScopes.has(scope.key));
  // Ocultar o último visível deixaria os nove painéis vazios sem explicação.
  if (visible.length === 1 && visible[0].key === scopeKey) return;
  if (_hiddenScopes.has(scopeKey)) _hiddenScopes.delete(scopeKey);
  else _hiddenScopes.add(scopeKey);
  _syncContextLabel();
  _applyScopeVisibility();
}

/**
 * Só a lista de células, disparada ao trocar de aba 4G/5G.
 *
 * Não reusa `_refreshScopeData` porque essa reaplica a semente de seleção
 * (herdada do dashboard) sempre que a comparação estiver vazia — o que
 * reintroduziria um escopo que o usuário acabou de limpar só por ter trocado
 * de tecnologia. Só a purga de células que deixaram de existir na família nova.
 */
async function _refreshCellsForFamily() {
  if (!State.eventId || !_isOpen()) return;
  try {
    const cells = await API.getEventCells(State.eventId, _family);
    if (!_isOpen()) return;
    _scopeCells = Array.isArray(cells) ? cells : _scopeCells;
    const before = _selectionKey();
    const cellIds = new Set(_scopeCells.map(cell => cell.id));
    [..._selection.cell].forEach(id => {
      if (!cellIds.has(id)) _selection.cell.delete(id);
    });
    _renderPickers();
    if (_selectionKey() !== before) void _load();
  } catch (error) {
    console.error("Erro ao atualizar células da visão geral:", error);
  }
}

async function _refreshScopeData() {
  if (!State.eventId || !_isOpen()) return;
  try {
    const timestamp = State.mode === "historical" ? State.historicalTimestamp : null;
    const [sites, clusters, cells] = await Promise.all([
      API.getSites(State.eventId, timestamp, State.selectedMetric, null),
      API.getClusters(State.eventId),
      API.getEventCells(State.eventId, _family),
    ]);
    if (!_isOpen()) return;
    _scopeSites = Array.isArray(sites) ? sites : _scopeSites;
    _scopeClusters = Array.isArray(clusters) ? clusters : _scopeClusters;
    _scopeCells = Array.isArray(cells) ? cells : _scopeCells;
    const before = _selectionKey();
    // Escopos que sumiram do evento (ou de família, no caso de células) não
    // podem continuar na comparação.
    const clusterIds = new Set(_scopeClusters.map(cluster => cluster.id));
    const siteIds = new Set(_scopeSites.map(site => site.id));
    const cellIds = new Set(_scopeCells.map(cell => cell.id));
    const carrierKeys = new Set();
    _scopeSites.forEach(site => (site.carriers || []).forEach(carrier => {
      carrierKeys.add(`${site.id}::${carrier.earfcn}`);
    }));
    [..._selection.cluster].forEach(id => {
      if (!clusterIds.has(id)) _selection.cluster.delete(id);
    });
    [..._selection.site].forEach(id => {
      if (!siteIds.has(id)) _selection.site.delete(id);
    });
    [..._selection.cell].forEach(id => {
      if (!cellIds.has(id)) _selection.cell.delete(id);
    });
    [..._selection.siteCarrier].forEach(key => {
      if (!carrierKeys.has(key)) _selection.siteCarrier.delete(key);
    });
    if (_pendingSeed || !_selectionSize()) {
      _pendingSeed = false;
      _applyPreferredSelection();
    }
    _renderPickers();
    if (_selectionKey() !== before) void _load();
  } catch (error) {
    console.error("Erro ao atualizar escopos da visão geral:", error);
  }
}

// ── Gráficos ───────────────────────────────────────────────────────

function _destroyCharts() {
  _charts.forEach(chart => chart.destroy());
  _charts.clear();
  _activeIndex = null;
  _hoveredPanelId = null;
}

function _renderPanelShells() {
  _destroyCharts();
  const grid = document.getElementById("kpi-overview-grid");
  if (!grid) return;
  grid.innerHTML = "";
  PANELS[_family].forEach(panel => {
    const article = document.createElement("article");
    article.className = `kpi-overview-card${panel.paired ? " is-paired" : ""}`;
    article.dataset.panelId = panel.id;
    article.dataset.metrics = panel.metrics.join(",");
    article.dataset.crosshairIndex = "";
    article.innerHTML = `
      <div class="kpi-overview-card-header">
        <h3>${_esc(panel.title)}</h3>
        <span class="kpi-overview-dash-hint"><i></i>DL<i class="dashed"></i>UL</span>
        <span class="kpi-overview-card-unit"></span>
      </div>
      <div class="kpi-overview-chart-wrap">
        <canvas data-overview-panel="${_esc(panel.id)}"></canvas>
        <div class="kpi-overview-no-data hidden">Sem dados no período</div>
        <div class="kpi-overview-tooltip hidden"></div>
      </div>`;
    grid.appendChild(article);
  });
}

function _showState(state, message="") {
  document.getElementById("kpi-overview-loading")?.classList.toggle("hidden", state !== "loading");
  const error = document.getElementById("kpi-overview-error");
  error?.classList.toggle("hidden", state !== "error");
  if (error && state === "error") error.textContent = message || "Não foi possível carregar os KPIs.";
}

async function _load() {
  if (!_isOpen()) return;
  const scopes = _selectedScopes();
  if (!State.eventId || !scopes.length) {
    _renderPanelShells();
    _showState("error", "Selecione ao menos um cluster, site ou célula para visualizar os KPIs.");
    return;
  }
  const requestId = ++_requestId;
  _showState("loading");
  try {
    const response = await API.getKpiOverviewMulti(
      State.eventId,
      scopes.map(scope => ({ scope: scope.scope, scope_id: scope.scopeId })),
      _family,
      _minutes,
    );
    if (requestId !== _requestId || !_isOpen()) return;
    if (!response?.ok) throw new Error(response?.error || "Resposta inválida da API");
    _showState("ready");
    _scopeMeta = new Map(scopes.map(scope => [scope.key, scope]));
    _renderCharts(response);
  } catch (error) {
    if (requestId !== _requestId) return;
    console.error("Erro ao carregar visão geral de KPIs:", error);
    _showState("error", error?.message || "Não foi possível carregar os KPIs.");
  }
}

function _renderCharts(response) {
  _destroyCharts();
  const labels = response.labels || [];
  const scopeCount = (response.series || []).length;
  PANELS[_family].forEach(panel => {
    const card = document.querySelector(`.kpi-overview-card[data-panel-id="${panel.id}"]`);
    const canvas = card?.querySelector("canvas");
    if (!card || !canvas) return;
    const datasets = _datasetsFor(panel, response, scopeCount);
    const hasData = datasets.some(dataset => dataset.data.some(value => value != null));
    card.classList.toggle("is-empty", !hasData);
    const emptyLabel = card.querySelector(".kpi-overview-no-data");
    emptyLabel?.classList.toggle("hidden", hasData);
    if (emptyLabel && !hasData) emptyLabel.textContent = _emptyMessage(panel, response);

    // B4/B5: um degrau de escala por painel — os dois traços de um par
    // dividem o eixo, então dividir a unidade também é obrigatório.
    const units = [...new Set(panel.metrics.map(metric => response.units?.[metric]).filter(Boolean))];
    const scale = escalaDaMetrica(
      `${_family}:${panel.id}`,
      datasets.map(dataset => dataset.data),
      units.length === 1 ? units[0] : "",
    );
    _panelScales.set(panel.id, scale);
    datasets.forEach(dataset => { dataset.unitLabel = scale.rotulo || dataset.unit; });
    card.querySelector(".kpi-overview-card-unit").textContent =
      units.length === 1 ? scale.rotulo : units.join(" / ");

    let chartRef = null;
    canvas.addEventListener("mousemove", event => {
      if (!chartRef) return;
      const elements = chartRef.getElementsAtEventForMode(event, "index", { intersect: false }, false);
      if (!elements.length) return;
      _hoveredPanelId = panel.id;
      _hideTooltips(panel.id);
      _syncCrosshairs(elements[0].index);
    }, { capture: true });
    canvas.addEventListener("mouseleave", _clearHover);

    chartRef = new Chart(canvas, {
      type: "line",
      plugins: [_crosshairPlugin],
      data: { labels, datasets },
      options: _chartOptions(panel, response, scale),
    });
    _charts.set(panel.id, chartRef);
  });
}

/**
 * B6 — painel vazio não é sempre a mesma coisa.
 *
 * "Sem dados" é ausência de coleta; "sem tráfego" é a métrica ficar indefinida
 * porque não houve tentativa nenhuma naquele minuto (denominador zero), o que é
 * o caso normal do SA no 5G. O backend decide por métrica em `reasons`; aqui só
 * se escolhe a frase — um painel pareado só está vazio quando os dois lados
 * estão, então basta que todos concordem para chamar de "sem tráfego".
 */
function _emptyMessage(panel, response) {
  const reasons = response.reasons || {};
  const noTraffic = panel.metrics.length
    && panel.metrics.every(metric => reasons[metric] === "no_traffic");
  return noTraffic ? "Sem tráfego no período" : "Sem dados no período";
}

/**
 * Um dataset por (escopo × métrica do painel). Nos painéis pareados, o segundo
 * traço é o UL: mesma cor do escopo, mesmo eixo, linha tracejada.
 */
function _datasetsFor(panel, response, scopeCount) {
  const datasets = [];
  (response.series || []).forEach(series => {
    const key = `${series.scope}:${series.scope_id}`;
    const meta = _scopeMeta.get(key) || { name: series.scope_id, color: SERIES_COLORS[0] };
    panel.metrics.forEach((metric, index) => {
      const isUl = !!panel.paired && index === 1;
      const values = series.metrics?.[metric];
      datasets.push({
        label: _datasetLabel(panel, meta, isUl, scopeCount),
        scopeKey: key,
        scopeName: meta.name,
        metric,
        unit: response.units?.[metric] || "",
        data: Array.isArray(values) ? values : (response.labels || []).map(() => null),
        yAxisID: "yLeft",
        borderColor: meta.color,
        backgroundColor: _hexToRgba(meta.color, 0.08),
        borderWidth: 2,
        borderDash: isUl ? UL_DASH : [],
        pointRadius: 0,
        pointHoverRadius: 3,
        tension: 0.25,
        // Área só quando existe um único traço — com dois ou mais, o
        // preenchimento embaralha as cores em vez de ajudar a leitura.
        fill: scopeCount === 1 && panel.metrics.length === 1,
        spanGaps: false,
        hidden: _hiddenScopes.has(key),
      });
    });
  });
  return datasets;
}

function _datasetLabel(panel, meta, isUl, scopeCount) {
  const side = isUl ? "UL" : "DL";
  if (scopeCount > 1) return panel.paired ? `${meta.name} · ${side}` : meta.name;
  return panel.paired ? side : panel.title;
}

function _chartOptions(panel, response, scale) {
  const primaryMetric = panel.metrics[0];
  const threshold = response.thresholds?.[primaryMetric] || {};
  // B2: o threshold vem normalizado como {value, unit}.
  const warnValue = Number(threshold.warning?.value);
  const critValue = Number(threshold.critical?.value);
  const annotations = {};
  if (Number.isFinite(warnValue)) {
    annotations.warning = _thresholdAnnotation(warnValue, "#D29922");
  }
  if (Number.isFinite(critValue)) {
    annotations.critical = _thresholdAnnotation(critValue, "#F85149");
  }
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    normalized: true,
    interaction: { mode: "index", intersect: false },
    plugins: {
      // A identidade dos escopos fica na legenda única do cabeçalho; nove
      // legendas iguais só roubariam área de plotagem dos cards.
      legend: { display: false },
      tooltip: {
        enabled: false,
        external: context => _renderTooltip(context, panel),
      },
      annotation: { annotations },
    },
    scales: {
      x: {
        grid: { color: "#21262D" },
        ticks: {
          color: "#8B949E", font: { size: 9 }, maxTicksLimit: 4, maxRotation: 0,
          callback: (_, index) => _formatTime(response.labels?.[index]),
        },
      },
      // Eixo único: DL e UL do mesmo painel compartilham a unidade, e um
      // segundo eixo faria escalas diferentes parecerem a mesma curva.
      yLeft: {
        position: "left",
        grid: { color: "#21262D" },
        ticks: {
          color: "#8B949E", font: { size: 9 }, maxTicksLimit: 4,
          callback: value => formatar(value, scale),
        },
      },
    },
  };
}

/** Ocultar um escopo pela legenda vale para os nove painéis de uma vez. */
function _applyScopeVisibility() {
  _charts.forEach(chart => {
    chart.data.datasets.forEach((dataset, index) => {
      chart.setDatasetVisibility(index, !_hiddenScopes.has(dataset.scopeKey));
    });
    chart.update();
  });
}

function _thresholdAnnotation(value, color) {
  return { type: "line", yMin: value, yMax: value, yScaleID: "yLeft", borderColor: color, borderWidth: 1, borderDash: [4, 3] };
}

function _syncCrosshairs(index) {
  if (_activeIndex === index && [..._charts.values()].every(chart => chart)) return;
  _activeIndex = index;
  document.querySelectorAll(".kpi-overview-card").forEach(card => {
    card.dataset.crosshairIndex = index == null ? "" : String(index);
  });
  _charts.forEach(chart => chart.render());
}

function _clearHover() {
  _hoveredPanelId = null;
  _activeIndex = null;
  document.querySelectorAll(".kpi-overview-card").forEach(card => { card.dataset.crosshairIndex = ""; });
  _hideTooltips();
  _charts.forEach(chart => chart.render());
}

function _hideTooltips(exceptPanelId=null) {
  document.querySelectorAll(".kpi-overview-card").forEach(card => {
    if (card.dataset.panelId !== exceptPanelId) {
      card.querySelector(".kpi-overview-tooltip")?.classList.add("hidden");
    }
  });
}

function _renderTooltip(context, panel) {
  const card = context.chart.canvas.closest(".kpi-overview-card");
  const tooltip = card?.querySelector(".kpi-overview-tooltip");
  const model = context.tooltip;
  if (!tooltip || _hoveredPanelId !== panel.id || model.opacity === 0 || !model.dataPoints?.length) {
    tooltip?.classList.add("hidden");
    return;
  }
  const index = model.dataPoints[0].dataIndex;
  const timestamp = context.chart.data.labels[index];
  const visible = context.chart.data.datasets
    .map((dataset, position) => ({ dataset, position }))
    .filter(({ position }) => context.chart.isDatasetVisible(position));
  const scale = _panelScales.get(panel.id) || null;
  const rows = visible.map(({ dataset }) => {
    const value = dataset.data[index];
    return `<div class="kpi-overview-tooltip-row">
      <span><i class="kpi-overview-tooltip-swatch" style="background:${_safeColor(dataset.borderColor)}"></i><em>${_esc(dataset.label)}</em></span>
      <strong>${value == null ? "—" : `${formatar(value, scale)} ${_esc(dataset.unitLabel || dataset.unit)}`}</strong>
    </div>`;
  }).join("");
  tooltip.classList.toggle("is-dense", visible.length > 4);
  tooltip.innerHTML = `<div class="kpi-overview-tooltip-time">${_esc(_formatDateTime(timestamp))}</div><div class="kpi-overview-tooltip-rows">${rows}</div>`;
  tooltip.classList.remove("hidden");
  const wrap = card.querySelector(".kpi-overview-chart-wrap");
  let left = model.caretX + 12;
  if (wrap && left + tooltip.offsetWidth + 6 > wrap.clientWidth) left = model.caretX - tooltip.offsetWidth - 12;
  tooltip.style.left = `${Math.max(2, left)}px`;
  // Comparando vários escopos o tooltip fica alto: prende dentro do card em vez
  // de deixá-lo vazar por baixo do gráfico.
  const maxTop = (wrap?.clientHeight || 0) - tooltip.offsetHeight - 4;
  tooltip.style.top = `${Math.min(Math.max(2, model.caretY - 8), Math.max(2, maxTop))}px`;
}

/** Cor validada como hex — vai para dentro de um atributo style. */
function _safeColor(value) {
  return /^#[\da-f]{3,8}$/i.test(String(value || "")) ? String(value) : SERIES_COLORS[0];
}

function _hexToRgba(hex, alpha) {
  const match = /^#?([\da-f]{6})$/i.exec(String(hex || ""));
  if (!match) return `rgba(56, 139, 253, ${alpha})`;
  const value = parseInt(match[1], 16);
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${alpha})`;
}

function _formatTime(value) {
  if (!value) return "";
  return new Date(value).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

function _formatDateTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function _esc(value) {
  const element = document.createElement("span");
  element.textContent = String(value ?? "");
  return element.innerHTML;
}

export const KPI_OVERVIEW_PANELS = PANELS;