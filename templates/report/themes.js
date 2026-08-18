/**
 * themes.js - Gerenciador de temas, presets e personalização de cores/banners
 */

const REPORT_THEMES = {
  // 1. Padrão Roxo (Referência 0B43EC70...)
  default_purple: {
    id: "default_purple",
    name: "Padrão Roxo & Azul (Default)",
    primary_color: "#1a052e",
    secondary_color: "#5b21b6",
    accent_color: "#9333ea",
    text_color_header: "#ffffff",
    bg_canvas: "#f4f5f8",
    bar_gradient: "linear-gradient(180deg, #6b21a8 0%, #3b0764 100%)",
    header_bg: "linear-gradient(135deg, #15002a 0%, #3b0764 45%, #180033 100%)",
    header_banner_url: "",
    header_overlay_gradient: "linear-gradient(90deg, rgba(21,0,42,0.85) 0%, rgba(59,7,100,0.6) 100%)",
    logo_type: "text",
    logo_title: "SMART EVENTS",
    logo_subtitle: "PERFORMANCE MONITORING",
    title_text: "PERFORMANCE",
    title_subtext: "DASHBOARD",
    title_box_style: "clean_modern",
    footer_bg: "linear-gradient(180deg, #1f0838 0%, #110022 100%)",
    health_colors: { good: "#1e7e48", warning: "#d48806", bad: "#b82c2c" }
  },

  // 2. Romaria Muquém (Referência 4A8DF605...)
  romaria_muquem: {
    id: "romaria_muquem",
    name: "Romaria Nossa Senhora da Abadia do Muquém",
    primary_color: "#0c2b55",
    secondary_color: "#1e3a8a",
    accent_color: "#d49b4b",
    text_color_header: "#ffffff",
    bg_canvas: "#f4f5f8",
    bar_gradient: "linear-gradient(180deg, #d4a047 0%, #a2721e 100%)",
    header_bg: "linear-gradient(135deg, #091f3d 0%, #0f386d 50%, #06172e 100%)",
    header_banner_url: "",
    header_overlay_gradient: "linear-gradient(90deg, rgba(9,31,61,0.9) 0%, rgba(15,56,109,0.7) 100%)",
    logo_type: "muquem",
    logo_title: "ROMARIA",
    logo_subtitle: "Nossa Senhora da Abadia do",
    logo_extra: "Muquém",
    title_text: "PERFORMANCE",
    title_subtext: "DASHBOARD",
    title_box_style: "golden_box_sparkles",
    footer_bg: "linear-gradient(180deg, #0c2b55 0%, #06162d 100%)",
    health_colors: { good: "#1e7e48", warning: "#d48806", bad: "#b82c2c" }
  },

  // 3. 52 Anos Exposul (Referência 5686D62C...)
  exposul: {
    id: "exposul",
    name: "52 Anos Exposul (Rústico / Madeira)",
    primary_color: "#3e2311",
    secondary_color: "#78431e",
    accent_color: "#d49b4b",
    text_color_header: "#ffffff",
    bg_canvas: "#f4f5f8",
    bar_gradient: "linear-gradient(180deg, #9c6032 0%, #683c18 100%)",
    header_bg: "linear-gradient(135deg, #422613 0%, #6b3d1f 50%, #381e0d 100%)",
    header_banner_url: "",
    header_overlay_gradient: "linear-gradient(90deg, rgba(66,38,19,0.85) 0%, rgba(107,61,31,0.65) 100%)",
    logo_type: "exposul",
    logo_title: "52 ANOS",
    logo_subtitle: "EXPOSUL",
    title_text: "PERFORMANCE",
    title_subtext: "DASHBOARD",
    title_box_style: "wood_rustic",
    footer_bg: "linear-gradient(180deg, #3e2311 0%, #221308 100%)",
    health_colors: { good: "#3b6e41", warning: "#d49b4b", bad: "#b82c2c" }
  },

  // 4. Capital Moto Week (Referência 4F8E0F13...)
  moto_week: {
    id: "moto_week",
    name: "Capital Moto Week 2026",
    primary_color: "#12091c",
    secondary_color: "#6b21a8",
    accent_color: "#ff6b00",
    text_color_header: "#ffffff",
    bg_canvas: "#f4f5f8",
    bar_gradient: "linear-gradient(180deg, #7e22ce 0%, #4c1d95 100%)",
    header_bg: "radial-gradient(ellipse at 15% 40%, #ff5500 0%, #4a044e 45%, #0d0414 100%)",
    header_banner_url: "",
    header_overlay_gradient: "linear-gradient(90deg, rgba(18,9,28,0.7) 0%, rgba(74,4,78,0.5) 100%)",
    logo_type: "motoweek",
    logo_title: "Capital",
    logo_subtitle: "MOTO WEEK",
    logo_extra: "2026",
    title_text: "PERFORMANCE",
    title_subtext: "DASHBOARD",
    title_box_style: "orange_glow",
    footer_bg: "linear-gradient(180deg, #1c0f2b 0%, #0c0414 100%)",
    health_colors: { good: "#1e7e48", warning: "#d48806", bad: "#b82c2c" }
  },

  // 5. Stock Car Pro Series (Referência 57B7EC37...)
  stock_car: {
    id: "stock_car",
    name: "Stock Car Pro Series",
    primary_color: "#0a192f",
    secondary_color: "#0f3b6d",
    accent_color: "#0284c7",
    text_color_header: "#ffffff",
    bg_canvas: "#f4f5f8",
    bar_gradient: "linear-gradient(180deg, #0f3b6d 0%, #051937 100%)",
    header_bg: "linear-gradient(135deg, #09182b 0%, #1b3459 50%, #05101f 100%)",
    header_banner_url: "",
    header_overlay_gradient: "linear-gradient(90deg, rgba(9,24,43,0.9) 0%, rgba(27,52,89,0.7) 100%)",
    logo_type: "stockcar",
    logo_title: "STOCK",
    logo_subtitle: "CAR",
    logo_extra: "PRO SERIES",
    title_text: "PERFORMANCE",
    title_subtext: "DASHBOARD",
    title_box_style: "racing_speed",
    footer_bg: "linear-gradient(180deg, #0a192f 0%, #030712 100%)",
    health_colors: { good: "#1e7e48", warning: "#d48806", bad: "#b82c2c" }
  },

  // 6. João Rock 2026 (Referência 731221E1...)
  joao_rock: {
    id: "joao_rock",
    name: "João Rock 2026",
    primary_color: "#3b0764",
    secondary_color: "#be185d",
    accent_color: "#f43f5e",
    text_color_header: "#ffffff",
    bg_canvas: "#f4f5f8",
    bar_gradient: "linear-gradient(180deg, #f472b6 0%, #be185d 100%)",
    header_bg: "linear-gradient(135deg, #059669 0%, #581c87 40%, #831843 100%)",
    header_banner_url: "",
    header_overlay_gradient: "linear-gradient(90deg, rgba(5,150,105,0.7) 0%, rgba(88,28,135,0.8) 100%)",
    logo_type: "joaorock",
    logo_title: "JOÃO",
    logo_subtitle: "ROCK",
    logo_extra: "2026",
    title_text: "PERFORMANCE",
    title_subtext: "DASHBOARD",
    title_box_style: "festival_vibrant",
    footer_bg: "linear-gradient(180deg, #3b0764 0%, #1f0438 100%)",
    health_colors: { good: "#1e7e48", warning: "#d48806", bad: "#b82c2c" }
  }
};

/**
 * Aplica as propriedades do tema nas CSS Variables do documento
 */
function applyReportTheme(themeConfig) {
  const root = document.documentElement;

  if (!themeConfig) return;

  // Atualiza CSS Variables fundamentais
  if (themeConfig.primary_color) root.style.setProperty("--theme-primary", themeConfig.primary_color);
  if (themeConfig.secondary_color) root.style.setProperty("--theme-secondary", themeConfig.secondary_color);
  if (themeConfig.accent_color) root.style.setProperty("--theme-accent", themeConfig.accent_color);
  if (themeConfig.bar_gradient) root.style.setProperty("--bar-gradient", themeConfig.bar_gradient);
  if (themeConfig.bg_canvas) root.style.setProperty("--bg-canvas", themeConfig.bg_canvas);
  if (themeConfig.footer_bg) root.style.setProperty("--footer-bg", themeConfig.footer_bg);

  // Health colors
  if (themeConfig.health_colors) {
    if (themeConfig.health_colors.good) root.style.setProperty("--health-good", themeConfig.health_colors.good);
    if (themeConfig.health_colors.warning) root.style.setProperty("--health-warning", themeConfig.health_colors.warning);
    if (themeConfig.health_colors.bad) root.style.setProperty("--health-bad", themeConfig.health_colors.bad);
  }

  // Header Banner Background
  const headerElem = document.getElementById("report-header");
  if (headerElem) {
    if (themeConfig.header_banner_url && themeConfig.header_banner_url.trim() !== "") {
      headerElem.style.backgroundImage = `url('${themeConfig.header_banner_url}')`;
      headerElem.style.backgroundSize = "cover";
      headerElem.style.backgroundPosition = "center";
    } else if (themeConfig.header_bg) {
      headerElem.style.background = themeConfig.header_bg;
    }
  }

  // Header Logo & Branding rendering
  renderHeaderBranding(themeConfig);

  // Footer branding
  const footerElem = document.getElementById("report-footer");
  if (footerElem && themeConfig.footer_bg) {
    footerElem.style.background = themeConfig.footer_bg;
  }
}

/**
 * Renderiza o bloco de branding (logo + título) no header
 */
function renderHeaderBranding(themeConfig) {
  const logoContainer = document.getElementById("header-logo-container");
  const titleContainer = document.getElementById("header-title-container");

  if (!logoContainer || !titleContainer) return;

  // Title rendering
  const titleText = themeConfig.title_text || "PERFORMANCE";
  const subtext = themeConfig.title_subtext || "DASHBOARD";
  const boxStyle = themeConfig.title_box_style || "clean_modern";

  let titleHtml = "";
  if (boxStyle === "golden_box_sparkles") {
    titleHtml = `
      <div class="header-title-box gold-border">
        <span class="main-title gold-text">${titleText}</span>
        <span class="sub-title gold-text">${subtext}</span>
        <span class="sparkle-deco">✦</span>
      </div>
    `;
  } else if (boxStyle === "orange_glow") {
    titleHtml = `
      <div class="header-title-box orange-glow">
        <span class="main-title orange-title">${titleText}</span>
        <span class="sub-title white-title">${subtext}</span>
      </div>
    `;
  } else if (boxStyle === "wood_rustic") {
    titleHtml = `
      <div class="header-title-box rustic">
        <span class="main-title rustic-title">${titleText}</span>
        <span class="sub-title rustic-subtitle">${subtext}</span>
      </div>
    `;
  } else if (boxStyle === "festival_vibrant") {
    titleHtml = `
      <div class="header-title-box festival">
        <span class="main-title festival-title">${titleText}</span>
        <span class="sub-title festival-subtitle">${subtext}</span>
      </div>
    `;
  } else {
    // clean modern
    titleHtml = `
      <div class="header-title-box default-style">
        <span class="main-title">${titleText}</span>
        <span class="sub-title">${subtext}</span>
      </div>
    `;
  }
  titleContainer.innerHTML = titleHtml;

  // Logo rendering
  const logoType = themeConfig.logo_type || "text";
  let logoHtml = "";

  if (themeConfig.custom_logo_url && themeConfig.custom_logo_url.trim() !== "") {
    logoHtml = `<img src="${themeConfig.custom_logo_url}" class="custom-header-logo" alt="Logo">`;
  } else if (logoType === "muquem") {
    logoHtml = `
      <div class="muquem-logo-brand">
        <div class="muquem-icon">
          <svg viewBox="0 0 60 70" width="55" height="65">
            <path d="M30 5 C35 15, 45 20, 48 30 C50 40, 40 60, 30 65 C20 60, 10 40, 12 30 C15 20, 25 15, 30 5 Z" fill="#d4af37" opacity="0.85"/>
            <circle cx="30" cy="22" r="7" fill="#ffffff"/>
            <path d="M22 35 Q30 28 38 35 Q38 52 30 58 Q22 52 22 35 Z" fill="#0c2b55"/>
            <circle cx="30" cy="7" r="3" fill="#ffe066"/>
          </svg>
        </div>
        <div class="muquem-text">
          <div class="muquem-tag">ROMARIA</div>
          <div class="muquem-sub">Nossa Senhora<br>da Abadia do</div>
          <div class="muquem-main">Muquém</div>
        </div>
      </div>
    `;
  } else if (logoType === "exposul") {
    logoHtml = `
      <div class="exposul-logo-brand">
        <div class="exposul-badge">
          <div class="exposul-circle">
            <span class="exp-number">52</span>
            <span class="exp-anos">ANOS</span>
            <span class="exp-title">EXPOSUL</span>
          </div>
        </div>
        <div class="exposul-bull-icon">
          <svg viewBox="0 0 70 60" width="65" height="55">
            <path d="M15 20 Q35 5 55 20 Q50 35 35 48 Q20 35 15 20 Z" fill="#683c18"/>
            <path d="M10 15 Q20 18 25 22 Q18 28 10 15 Z" fill="#d49b4b"/>
            <path d="M60 15 Q50 18 45 22 Q52 28 60 15 Z" fill="#d49b4b"/>
            <circle cx="28" cy="28" r="3" fill="#000"/>
            <circle cx="42" cy="28" r="3" fill="#000"/>
          </svg>
        </div>
      </div>
    `;
  } else if (logoType === "motoweek") {
    logoHtml = `
      <div class="motoweek-logo-brand">
        <div class="motoweek-badge">
          <div class="mw-circle">
            <span class="mw-script">Capital</span>
            <span class="mw-bold">MOTO WEEK</span>
            <span class="mw-year">2026</span>
          </div>
        </div>
      </div>
    `;
  } else if (logoType === "stockcar") {
    logoHtml = `
      <div class="stockcar-logo-brand">
        <div class="sc-main">
          <span class="sc-stock">STOCK</span>
          <span class="sc-car">CAR</span>
        </div>
        <div class="sc-sub">PRO SERIES</div>
      </div>
    `;
  } else if (logoType === "joaorock") {
    logoHtml = `
      <div class="joaorock-logo-brand">
        <div class="jr-badge">
          <div class="jr-top">JOÃO</div>
          <div class="jr-bottom">ROCK</div>
          <div class="jr-year">2026</div>
        </div>
        <div class="jr-chameleon">
          <svg viewBox="0 0 65 60" width="60" height="55">
            <path d="M15 45 Q20 15 45 20 Q55 30 45 45 Q30 55 15 45 Z" fill="#ec4899"/>
            <circle cx="42" cy="25" r="7" fill="#a3e635"/>
            <circle cx="44" cy="24" r="3" fill="#000"/>
            <path d="M30 35 L55 38 L30 42 Z" fill="#eab308"/>
          </svg>
        </div>
      </div>
    `;
  } else {
    // default smart events
    logoHtml = `
      <div class="default-event-brand">
        <div class="brand-icon">
          <svg viewBox="0 0 36 36" width="36" height="36">
            <rect width="36" height="36" rx="8" fill="url(#brandGrad)"/>
            <defs>
              <linearGradient id="brandGrad" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stop-color="#9333ea"/>
                <stop offset="100%" stop-color="#3b0764"/>
              </linearGradient>
            </defs>
            <path d="M10 18 L16 24 L26 12" stroke="#ffffff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
          </svg>
        </div>
        <div class="brand-text">
          <span class="brand-title">SMART EVENTS</span>
          <span class="brand-sub">PERFORMANCE SUITE</span>
        </div>
      </div>
    `;
  }

  logoContainer.innerHTML = logoHtml;
}

// Export for module/script usage
if (typeof module !== "undefined" && module.exports) {
  module.exports = { REPORT_THEMES, applyReportTheme };
}
