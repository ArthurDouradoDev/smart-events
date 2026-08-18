/**
 * report.js - Engine de renderização de componentes, gráficos e interatividade
 */

// Estado global do relatório
let currentThemeId = "default_purple";
let currentDatasetKey = "default_purple";

document.addEventListener("DOMContentLoaded", () => {
  // Inicializa dados e tema
  initReport();
  setupCustomizerToolbar();
});

/**
 * Inicialização principal do relatório
 */
function initReport() {
  const dataset = REPORT_DATASETS[currentDatasetKey] || REPORT_DATASETS.default_purple;
  const theme = REPORT_THEMES[currentThemeId] || REPORT_THEMES.default_purple;

  applyReportTheme(theme);
  renderReportData(dataset);
}

/**
 * Renderiza todos os dados do relatório no canvas
 */
function renderReportData(data) {
  if (!data) return;

  // 1. Renderiza Seção 5G
  if (data.tech_5g) {
    renderGauges("5g", data.tech_5g.gauges);
    render5GChartsAndStack(data.tech_5g.charts, data.tech_5g.metrics_stack, data.period.hours);
    renderHealthCards("5g", data.tech_5g.health_cards);
  }

  // 2. Renderiza Seção 4G
  if (data.tech_4g) {
    renderGauges("4g", data.tech_4g.gauges);
    render4GChartsAndVolte(data.tech_4g.charts, data.tech_4g.volte_table, data.period.hours);
    renderHealthCards("4g", data.tech_4g.health_cards);
  }
}

/**
 * Renderiza os 5 velocímetros (Gauges) de uma tecnologia (5G ou 4G)
 */
function renderGauges(tech, gauges) {
  const container = document.getElementById(`gauges-row-${tech}`);
  if (!container || !gauges) return;

  container.innerHTML = "";

  const gaugeKeys = ["throughput_dl", "throughput_ul", "prb_dl", "prb_ul", "interference"];
  gaugeKeys.forEach((key) => {
    const g = gauges[key];
    if (!g) return;

    const gaugeElem = document.createElement("div");
    gaugeElem.className = "gauge-item";
    gaugeElem.id = `gauge-${tech}-${key}`;

    const svgHtml = createGaugeSVG(g, key);

    gaugeElem.innerHTML = `
      <div class="gauge-label">${g.label}</div>
      <div class="gauge-svg-container">${svgHtml}</div>
      <div class="gauge-value-label">${g.value} ${g.unit}</div>
    `;

    container.appendChild(gaugeElem);
  });
}

/**
 * Gera o SVG do velocímetro com arcos por SLA e agulha rotacionada
 */
function createGaugeSVG(gaugeData, gaugeType) {
  const { value, min, max } = gaugeData;
  const cx = 70;
  const cy = 68;
  const r = 50;
  const strokeWidth = 30;

  // Normalização do valor entre 0 e 1
  let clampedVal = Math.max(min, Math.min(max, value));
  let normalized = (clampedVal - min) / (max - min);
  if (isNaN(normalized)) normalized = 0.5;

  // Ângulos do semicírculo: de -180° (esquerda) até 0° (direita)
  const needleAngle = -180 + normalized * 180;

  // Definição das zonas de cor dos arcos
  let arcsHtml = "";
  if (gaugeType.includes("throughput")) {
    // Throughput: Vermelho (0-25%), Amarelo (25-50%), Verde (50-100%)
    arcsHtml = `
      ${describeSVGArc(cx, cy, r, -180, -135, "var(--gauge-red)", strokeWidth)}
      ${describeSVGArc(cx, cy, r, -135, -90, "var(--gauge-yellow)", strokeWidth)}
      ${describeSVGArc(cx, cy, r, -90, 0, "var(--gauge-green)", strokeWidth)}
    `;
  } else if (gaugeType.includes("prb")) {
    // PRB: Verde (0-50%), Amarelo (50-75%), Vermelho (75-100%)
    arcsHtml = `
      ${describeSVGArc(cx, cy, r, -180, -90, "var(--gauge-green)", strokeWidth)}
      ${describeSVGArc(cx, cy, r, -90, -45, "var(--gauge-yellow)", strokeWidth)}
      ${describeSVGArc(cx, cy, r, -45, 0, "var(--gauge-red)", strokeWidth)}
    `;
  } else {
    // Interferência: Verde (-125 a -105 dBm -> 0 a 50%), Amarelo (50-75%), Vermelho (75-100%)
    arcsHtml = `
      ${describeSVGArc(cx, cy, r, -180, -90, "var(--gauge-green)", strokeWidth)}
      ${describeSVGArc(cx, cy, r, -90, -45, "var(--gauge-yellow)", strokeWidth)}
      ${describeSVGArc(cx, cy, r, -45, 0, "var(--gauge-red)", strokeWidth)}
    `;
  }

  // Cálculo da ponta da agulha
  const rad = (needleAngle * Math.PI) / 180;
  const needleLength = 40;
  const tipX = cx + needleLength * Math.cos(rad);
  const tipY = cy + needleLength * Math.sin(rad);

  return `
    <svg viewBox="0 0 140 80" width="140" height="80">
      <g>
        ${arcsHtml}
      </g>
      <!-- Agulha preta -->
      <line x1="${cx}" y1="${cy}" x2="${tipX}" y2="${tipY}" stroke="#111827" stroke-width="3" stroke-linecap="round"/>
      <circle cx="${cx}" cy="${cy}" r="5" fill="#111827"/>
      <circle cx="${cx}" cy="${cy}" r="2" fill="#ffffff"/>
    </svg>
  `;
}

/**
 * Utilitário para traçar arcos SVG paramétricos
 */
function describeSVGArc(cx, cy, r, startAngleDeg, endAngleDeg, strokeColor, strokeWidth) {
  const startRad = (startAngleDeg * Math.PI) / 180;
  const endRad = (endAngleDeg * Math.PI) / 180;

  const x1 = cx + r * Math.cos(startRad);
  const y1 = cy + r * Math.sin(startRad);
  const x2 = cx + r * Math.cos(endRad);
  const y2 = cy + r * Math.sin(endRad);

  const largeArcFlag = endAngleDeg - startAngleDeg <= 180 ? "0" : "1";

  const d = [
    "M", x1.toFixed(2), y1.toFixed(2),
    "A", r, r, 0, largeArcFlag, 1, x2.toFixed(2), y2.toFixed(2)
  ].join(" ");

  return `<path d="${d}" fill="none" stroke="${strokeColor}" stroke-width="${strokeWidth}" stroke-linecap="butt"/>`;
}

/**
 * Renderiza a linha média do 5G (4 gráficos de barra + 1 stack de 4 métricas)
 */
function render5GChartsAndStack(charts, stackMetrics, hours) {
  const container = document.getElementById("middle-row-5g");
  if (!container) return;

  container.innerHTML = "";

  // Renderiza os 4 gráficos de barra
  if (charts && Array.isArray(charts)) {
    charts.forEach((chart) => {
      const chartCard = createBarChartCard(chart, hours);
      container.appendChild(chartCard);
    });
  }

  // Renderiza a stack de métricas no lado direito
  if (stackMetrics && Array.isArray(stackMetrics)) {
    const stackCol = document.createElement("div");
    stackCol.className = "metrics-stack-column";

    stackMetrics.forEach((m) => {
      const item = document.createElement("div");
      item.className = "metric-stack-item";
      item.innerHTML = `
        <div class="stack-value-box">${m.value}</div>
        <div class="stack-label-pill">${m.label}</div>
      `;
      stackCol.appendChild(item);
    });

    container.appendChild(stackCol);
  }
}

/**
 * Renderiza a linha média do 4G (3 gráficos de barra + 1 tabela VoLTE)
 */
function render4GChartsAndVolte(charts, volteRows, hours) {
  const container = document.getElementById("middle-row-4g");
  if (!container) return;

  container.innerHTML = "";

  // Renderiza os 3 gráficos de barra
  if (charts && Array.isArray(charts)) {
    charts.forEach((chart) => {
      const chartCard = createBarChartCard(chart, hours);
      container.appendChild(chartCard);
    });
  }

  // Renderiza a tabela de KPIs VoLTE no lado direito
  if (volteRows && Array.isArray(volteRows)) {
    const tableCol = document.createElement("div");
    tableCol.className = "volte-table-column";

    let rowsHtml = "";
    volteRows.forEach((row) => {
      const isBad = row.status === "bad" ? "bad-status" : "";
      rowsHtml += `
        <tr>
          <td class="volte-label">${row.label}</td>
          <td class="volte-val ${isBad}">${row.value}</td>
        </tr>
      `;
    });

    tableCol.innerHTML = `
      <div class="volte-header-pill">KPIs VoLTE</div>
      <table class="volte-table">
        <tbody>
          ${rowsHtml}
        </tbody>
      </table>
    `;

    container.appendChild(tableCol);
  }
}

/**
 * Cria o elemento HTML de um Card de Gráfico de Barras
 */
function createBarChartCard(chart, hours) {
  const card = document.createElement("div");
  card.className = "bar-chart-card";

  const rawValues = chart.raw_values || [100, 100, 100];
  const maxRaw = Math.max(...rawValues, 1);
  const maxBarHeightPx = 150; // altura máxima utilizável da barra

  let barsHtml = "";
  chart.values.forEach((valStr, i) => {
    const raw = rawValues[i] || 0;
    // Cálculo proporcional de altura (mínimo de 12px para visualização)
    const heightPx = Math.max(12, Math.round((raw / maxRaw) * maxBarHeightPx));

    barsHtml += `
      <div class="chart-bar-group">
        <div class="bar-value">${valStr}</div>
        <div class="bar-fill" style="height: ${heightPx}px;"></div>
      </div>
    `;
  });

  const hourList = hours || ["13:00", "14:00", "15:00"];
  let ticksHtml = "";
  hourList.forEach((h) => {
    ticksHtml += `<div class="hour-tick">${h}</div>`;
  });

  card.innerHTML = `
    <div class="chart-title">${chart.title}</div>
    <div class="chart-bars-area">
      ${barsHtml}
    </div>
    <div class="chart-hour-labels">
      ${ticksHtml}
    </div>
  `;

  return card;
}

/**
 * Renderiza a linha de 6 Cards de Saúde (Semáforo)
 */
function renderHealthCards(tech, cards) {
  const container = document.getElementById(`health-cards-row-${tech}`);
  if (!container || !cards) return;

  container.innerHTML = "";

  cards.forEach((c) => {
    const cardElem = document.createElement("div");
    cardElem.className = `health-card ${c.status || "good"}`;
    cardElem.innerHTML = `
      <div class="health-card-label">${c.label}</div>
      <div class="health-card-value">${c.value}</div>
    `;
    container.appendChild(cardElem);
  });
}

/**
 * Configuração da Toolbar Interativa de Customização
 */
function setupCustomizerToolbar() {
  const selectTheme = document.getElementById("select-theme-preset");
  const colorPrimary = document.getElementById("color-primary");
  const colorSecondary = document.getElementById("color-secondary");
  const colorAccent = document.getElementById("color-accent");
  const inputBannerUrl = document.getElementById("input-banner-url");
  const inputBand1Url = document.getElementById("input-band-1-url");
  const inputBand2Url = document.getElementById("input-band-2-url");
  const inputBand3Url = document.getElementById("input-band-3-url");
  const btnToggleExport = document.getElementById("btn-toggle-export");

  if (!selectTheme) return;

  // Popula seletor de presets
  selectTheme.innerHTML = "";
  Object.keys(REPORT_THEMES).forEach((key) => {
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent = REPORT_THEMES[key].name;
    if (key === currentThemeId) opt.selected = true;
    selectTheme.appendChild(opt);
  });

  // Listener para troca de tema
  selectTheme.addEventListener("change", (e) => {
    const newThemeId = e.target.value;
    currentThemeId = newThemeId;
    currentDatasetKey = newThemeId; // carrega o dataset correspondente

    const theme = REPORT_THEMES[newThemeId];
    const dataset = REPORT_DATASETS[newThemeId] || REPORT_DATASETS.default_purple;

    // Atualiza pickers
    if (colorPrimary && theme.primary_color) colorPrimary.value = theme.primary_color;
    if (colorSecondary && theme.secondary_color) colorSecondary.value = theme.secondary_color;
    if (colorAccent && theme.accent_color) colorAccent.value = theme.accent_color;

    applyReportTheme(theme);
    renderReportData(dataset);
  });

  // Listeners para customização de cores
  if (colorPrimary) {
    colorPrimary.addEventListener("input", (e) => {
      document.documentElement.style.setProperty("--theme-primary", e.target.value);
    });
  }

  if (colorSecondary) {
    colorSecondary.addEventListener("input", (e) => {
      document.documentElement.style.setProperty("--theme-secondary", e.target.value);
    });
  }

  if (colorAccent) {
    colorAccent.addEventListener("input", (e) => {
      document.documentElement.style.setProperty("--theme-accent", e.target.value);
    });
  }

  // Função utilitária para aplicar bg-image
  const applyBgImage = (inputElem, targetId) => {
    if (inputElem) {
      inputElem.addEventListener("change", (e) => {
        const url = e.target.value.trim();
        const target = document.getElementById(targetId);
        if (target) {
          if (url) {
            target.style.backgroundImage = `url('${url}')`;
            target.style.backgroundSize = "cover";
            target.style.backgroundPosition = "center";
          } else {
            target.style.backgroundImage = "none";
          }
        }
      });
    }
  };

  // Aplica aos elementos correspondentes
  applyBgImage(inputBannerUrl, "report-header");
  applyBgImage(inputBand1Url, "pre-band-1");
  applyBgImage(inputBand2Url, "pre-band-2");
  applyBgImage(inputBand3Url, "pre-band-3");

  const btnDownload = document.getElementById("btn-download");
  
  // Botão de download PNG
  if (btnDownload) {
    btnDownload.addEventListener("click", () => {
      const canvasElem = document.getElementById("report-canvas");
      if (!canvasElem || typeof html2canvas === 'undefined') {
        alert("O html2canvas não foi carregado corretamente ou o canvas não foi encontrado.");
        return;
      }
      
      const originalText = btnDownload.textContent;
      btnDownload.textContent = "Gerando PNG...";
      btnDownload.disabled = true;

      // Usando setTimeout para dar tempo ao browser de renderizar o texto de loading
      setTimeout(() => {
        html2canvas(canvasElem, {
          scale: 2, // Alta qualidade
          useCORS: true, // Permite carregar imagens externas se tiverem headers corretos
          backgroundColor: "#f4f5f8"
        }).then(canvas => {
          const link = document.createElement("a");
          link.download = `Relatorio_Performance_${currentThemeId}.png`;
          link.href = canvas.toDataURL("image/png");
          link.click();
          
          btnDownload.textContent = originalText;
          btnDownload.disabled = false;
        }).catch(err => {
          console.error("Erro ao gerar imagem:", err);
          alert("Erro ao gerar o PNG.");
          btnDownload.textContent = originalText;
          btnDownload.disabled = false;
        });
      }, 50);
    });
  }

  // Botão de alternar modo exportação / snapshot
  if (btnToggleExport) {
    btnToggleExport.addEventListener("click", () => {
      document.body.classList.toggle("export-mode");
      if (document.body.classList.contains("export-mode")) {
        btnToggleExport.textContent = "Voltar ao Editor";
      } else {
        btnToggleExport.textContent = "Modo Snapshot (Limpo)";
      }
    });
  }
}

// API Global para automação e testes
window.renderReport = function (dataset, theme) {
  if (theme) applyReportTheme(theme);
  if (dataset) renderReportData(dataset);
};

window.setReportTheme = function (theme) {
  applyReportTheme(theme);
};

window.setReportData = function (dataset) {
  renderReportData(dataset);
};
