/**
 * sample_data.js - Datasets estáticos para demonstração e paridade com as referências
 */

const REPORT_DATASETS = {
  // 1. Padrão / Default (Referência 0B43EC70...)
  default_purple: {
    event_name: "Smart Events Demo",
    report_title: "PERFORMANCE DASHBOARD",
    report_subtitle: "",
    period: {
      date: "2026-08-18",
      hours: ["13:00", "14:00", "15:00"],
      current_hour: "15:00"
    },
    tech_5g: {
      gauges: {
        throughput_dl: { label: "Taxa de Dados Direto", value: 130.18, unit: "Mbps", min: 0, max: 300 },
        throughput_ul: { label: "Taxa de Dados Reverso", value: 12.70, unit: "Mbps", min: 0, max: 50 },
        prb_dl: { label: "PRB DL", value: 7.57, unit: "%", min: 0, max: 100 },
        prb_ul: { label: "PRB UL", value: 17.73, unit: "%", min: 0, max: 100 },
        interference: { label: "Interferência", value: -111.36, unit: "dBm", min: -125, max: -85 }
      },
      charts: [
        { id: "users_nsa", title: "Usuários NSA", values: ["2.05k", "1.89k", "1.67k"], raw_values: [2050, 1890, 1670] },
        { id: "users_sa", title: "Usuários SA", values: ["949", "885", "786"], raw_values: [949, 885, 786] },
        { id: "volume_dl", title: "Volume Direto\n(GB)", values: ["310", "299", "277"], raw_values: [310, 299, 277] },
        { id: "volume_ul", title: "Volume Reverso\n(GB)", values: ["69.6", "72.1", "74.9"], raw_values: [69.6, 72.1, 74.9] }
      ],
      metrics_stack: [
        { label: "Reassembly Fail Rate", value: "0 %", status: "ok" },
        { label: "TX Drop", value: "0 %", status: "ok" },
        { label: "Delta VQI", value: "0.07 %", status: "ok" },
        { label: "Frag. TX Packets", value: "0.28 %", status: "ok" }
      ],
      health_cards: [
        { label: "Acessibilidade NSA", value: "99.57 %", status: "good" },
        { label: "Drop NSA", value: "0.07 %", status: "good" },
        { label: "Intra-SgNB Pscell", value: "99.9 %", status: "good" },
        { label: "Inter-SgNB Pscell", value: "99.02 %", status: "good" },
        { label: "Availability", value: "96 %", status: "bad" },
        { label: "Transmission", value: "0 %", status: "good" }
      ]
    },
    tech_4g: {
      gauges: {
        throughput_dl: { label: "Taxa de Dados Direto", value: 24.79, unit: "Mbps", min: 0, max: 100 },
        throughput_ul: { label: "Taxa de Dados Reverso", value: 6.23, unit: "Mbps", min: 0, max: 30 },
        prb_dl: { label: "PRB DL", value: 14.72, unit: "%", min: 0, max: 100 },
        prb_ul: { label: "PRB UL", value: 24.94, unit: "%", min: 0, max: 100 },
        interference: { label: "Interferência", value: -115.34, unit: "dBm", min: -125, max: -85 }
      },
      charts: [
        { id: "users_4g", title: "Usuários", values: ["4.76k", "4.31k", "3.79k"], raw_values: [4760, 4310, 3790] },
        { id: "volume_dl_4g", title: "Volume Direto\n(GB)", values: ["232", "220", "213"], raw_values: [232, 220, 213] },
        { id: "volume_ul_4g", title: "Volume Reverso\n(GB)", values: ["98.8", "109", "89.4"], raw_values: [98.8, 109, 89.4] }
      ],
      volte_table: [
        { label: "Efic ERAB QCI1 (%)", value: "100", status: "good" },
        { label: "Efic ERAB QCI5 (%)", value: "99.7", status: "good" },
        { label: "Efic SRVCC (%)", value: "—", status: "neutral" },
        { label: "Efic HO VOIP (%)", value: "100", status: "good" },
        { label: "Drop ERAB QCI1 (%)", value: "0.3", status: "good" },
        { label: "Drop ERAB QCI5 (%)", value: "0", status: "good" },
        { label: "UE Ativo QCI1", value: "1.5", status: "good" },
        { label: "Taxa Dados DL QCI1 (kbps)", value: "169.9", status: "good" },
        { label: "Taxa Dados UL QCI1 (kbps)", value: "405.5", status: "good" }
      ],
      health_cards: [
        { label: "Acessibilidade", value: "99.63 %", status: "good" },
        { label: "Drop Dados", value: "0.14 %", status: "good" },
        { label: "Handover", value: "99.87 %", status: "good" },
        { label: "CSFB", value: "100 %", status: "good" },
        { label: "Availability", value: "99.99 %", status: "good" },
        { label: "Transmission", value: "8.83 %", status: "good" }
      ]
    }
  },

  // 2. Romaria Muquém (Referência 4A8DF605...)
  romaria_muquem: {
    event_name: "ROMARIA Nossa Senhora da Abadia do Muquém",
    report_title: "PERFORMANCE DASHBOARD",
    report_subtitle: "✨",
    period: {
      date: "2026-08-18",
      hours: ["17:00", "18:00", "19:00"],
      current_hour: "19:00"
    },
    tech_5g: {
      gauges: {
        throughput_dl: { label: "Taxa de Dados Direto", value: 151.91, unit: "Mbps", min: 0, max: 300 },
        throughput_ul: { label: "Taxa de Dados Reverso", value: 12.86, unit: "Mbps", min: 0, max: 50 },
        prb_dl: { label: "PRB DL", value: 29.88, unit: "%", min: 0, max: 100 },
        prb_ul: { label: "PRB UL", value: 66.41, unit: "%", min: 0, max: 100 },
        interference: { label: "Interferência", value: -105.00, unit: "dBm", min: -125, max: -85 }
      },
      charts: [
        { id: "users_nsa", title: "Usuários NSA", values: ["471", "494", "488"], raw_values: [471, 494, 488] },
        { id: "users_sa", title: "Usuários SA", values: ["280", "295", "281"], raw_values: [280, 295, 281] },
        { id: "volume_dl", title: "Volume Direto\n(GB)", values: ["79.1", "81.7", "93.2"], raw_values: [79.1, 81.7, 93.2] },
        { id: "volume_ul", title: "Volume Reverso\n(GB)", values: ["18.6", "26.5", "21.8"], raw_values: [18.6, 26.5, 21.8] }
      ],
      metrics_stack: [
        { label: "Reassembly Fail Rate", value: "0 %", status: "ok" },
        { label: "TX Drop", value: "0 %", status: "ok" },
        { label: "Delta VQI", value: "0 %", status: "ok" },
        { label: "Frag. TX Packets", value: "0.87 %", status: "ok" }
      ],
      health_cards: [
        { label: "Acessibilidade NSA", value: "99.65 %", status: "good" },
        { label: "Drop NSA", value: "0 %", status: "good" },
        { label: "Intra-SgNB Pscell", value: "99.96 %", status: "good" },
        { label: "Inter-SgNB Pscell", value: "0 %", status: "bad" },
        { label: "Availability", value: "100 %", status: "good" },
        { label: "Transmission", value: "53.86 %", status: "good" }
      ]
    },
    tech_4g: {
      gauges: {
        throughput_dl: { label: "Taxa de Dados Direto", value: 14.39, unit: "Mbps", min: 0, max: 100 },
        throughput_ul: { label: "Taxa de Dados Reverso", value: 7.38, unit: "Mbps", min: 0, max: 30 },
        prb_dl: { label: "PRB DL", value: 60.65, unit: "%", min: 0, max: 100 },
        prb_ul: { label: "PRB UL", value: 56.79, unit: "%", min: 0, max: 100 },
        interference: { label: "Interferência", value: -113.75, unit: "dBm", min: -125, max: -85 }
      },
      charts: [
        { id: "users_4g", title: "Usuários", values: ["1.50k", "1.54k", "1.45k"], raw_values: [1500, 1540, 1450] },
        { id: "volume_dl_4g", title: "Volume Direto\n(GB)", values: ["100", "103", "102"], raw_values: [100, 103, 102] },
        { id: "volume_ul_4g", title: "Volume Reverso\n(GB)", values: ["31.6", "37.1", "35.6"], raw_values: [31.6, 37.1, 35.6] }
      ],
      volte_table: [
        { label: "Efic ERAB QCI1 (%)", value: "100", status: "good" },
        { label: "Efic ERAB QCI5 (%)", value: "99.9", status: "good" },
        { label: "Efic SRVCC (%)", value: "—", status: "neutral" },
        { label: "Efic HO VOIP (%)", value: "100", status: "good" },
        { label: "Drop ERAB QCI1 (%)", value: "0", status: "good" },
        { label: "Drop ERAB QCI5 (%)", value: "0", status: "good" },
        { label: "UE Ativo QCI1", value: "0.49", status: "good" },
        { label: "Taxa Dados DL QCI1 (kbps)", value: "58.6", status: "good" },
        { label: "Taxa Dados UL QCI1 (kbps)", value: "397.9", status: "good" }
      ],
      health_cards: [
        { label: "Acessibilidade", value: "99.83 %", status: "good" },
        { label: "Drop Dados", value: "0.09 %", status: "good" },
        { label: "Handover", value: "99.88 %", status: "good" },
        { label: "CSFB", value: "100 %", status: "good" },
        { label: "Availability", value: "100 %", status: "good" },
        { label: "Transmission", value: "13.46 %", status: "good" }
      ]
    }
  },

  // 3. Exposul (Referência 5686D62C...)
  exposul: {
    event_name: "52 ANOS EXPOSUL",
    report_title: "PERFORMANCE DASHBOARD",
    report_subtitle: "",
    period: {
      date: "2026-08-18",
      hours: ["23:00", "00:00", "01:00"],
      current_hour: "01:00"
    },
    tech_5g: {
      gauges: {
        throughput_dl: { label: "Taxa de Dados Direto", value: 362.55, unit: "Mbps", min: 0, max: 500 },
        throughput_ul: { label: "Taxa de Dados Reverso", value: 52.77, unit: "Mbps", min: 0, max: 80 },
        prb_dl: { label: "PRB DL", value: 2.20, unit: "%", min: 0, max: 100 },
        prb_ul: { label: "PRB UL", value: 7.57, unit: "%", min: 0, max: 100 },
        interference: { label: "Interferência", value: -115.00, unit: "dBm", min: -125, max: -85 }
      },
      charts: [
        { id: "users_nsa", title: "Usuários NSA", values: ["178", "132", "105"], raw_values: [178, 132, 105] },
        { id: "users_sa", title: "Usuários SA", values: ["2.61", "1.97", "1.75"], raw_values: [2.61, 1.97, 1.75] },
        { id: "volume_dl", title: "Volume Direto\n(GB)", values: ["23.4", "10.8", "5.10"], raw_values: [23.4, 10.8, 5.1] },
        { id: "volume_ul", title: "Volume Reverso\n(GB)", values: ["2.17", "2.84", "0.60"], raw_values: [2.17, 2.84, 0.6] }
      ],
      metrics_stack: [
        { label: "Reassembly Fail Rate", value: "0 %", status: "ok" },
        { label: "TX Drop", value: "0 %", status: "ok" },
        { label: "Delta VQI", value: "0 %", status: "ok" },
        { label: "Frag. TX Packets", value: "0.03 %", status: "ok" }
      ],
      health_cards: [
        { label: "Acessibilidade NSA", value: "99.96 %", status: "good" },
        { label: "Drop NSA", value: "0 %", status: "good" },
        { label: "Intra-SgNB Pscell", value: "100 %", status: "good" },
        { label: "Inter-SgNB Pscell", value: "99.24 %", status: "good" },
        { label: "Availability", value: "100 %", status: "good" },
        { label: "Transmission", value: "0 %", status: "good" }
      ]
    },
    tech_4g: {
      gauges: {
        throughput_dl: { label: "Taxa de Dados Direto", value: 40.38, unit: "Mbps", min: 0, max: 100 },
        throughput_ul: { label: "Taxa de Dados Reverso", value: 5.74, unit: "Mbps", min: 0, max: 30 },
        prb_dl: { label: "PRB DL", value: 3.09, unit: "%", min: 0, max: 100 },
        prb_ul: { label: "PRB UL", value: 8.94, unit: "%", min: 0, max: 100 },
        interference: { label: "Interferência", value: -118.26, unit: "dBm", min: -125, max: -85 }
      },
      charts: [
        { id: "users_4g", title: "Usuários", values: ["699", "511", "424"], raw_values: [699, 511, 424] },
        { id: "volume_dl_4g", title: "Volume Direto\n(GB)", values: ["35.0", "22.3", "17.3"], raw_values: [35.0, 22.3, 17.3] },
        { id: "volume_ul_4g", title: "Volume Reverso\n(GB)", values: ["6.84", "3.63", "5.47"], raw_values: [6.84, 3.63, 5.47] }
      ],
      volte_table: [
        { label: "Efic ERAB QCI1 (%)", value: "100", status: "good" },
        { label: "Efic ERAB QCI5 (%)", value: "99.9", status: "good" },
        { label: "Efic SRVCC (%)", value: "—", status: "neutral" },
        { label: "Efic HO VOIP (%)", value: "0", status: "bad" },
        { label: "Drop ERAB QCI1 (%)", value: "0", status: "good" },
        { label: "Drop ERAB QCI5 (%)", value: "0.1", status: "good" },
        { label: "UE Ativo QCI1", value: "0.03", status: "good" },
        { label: "Taxa Dados DL QCI1 (kbps)", value: "241", status: "good" },
        { label: "Taxa Dados UL QCI1 (kbps)", value: "349.7", status: "good" }
      ],
      health_cards: [
        { label: "Acessibilidade", value: "99.71 %", status: "good" },
        { label: "Drop Dados", value: "0.12 %", status: "good" },
        { label: "Handover", value: "97.52 %", status: "warning" },
        { label: "CSFB", value: "100 %", status: "good" },
        { label: "Availability", value: "100 %", status: "good" },
        { label: "Transmission", value: "0.5 %", status: "good" }
      ]
    }
  },

  // 4. Moto Week (Referência 4F8E0F13...)
  moto_week: {
    event_name: "Capital MOTO WEEK 2026",
    report_title: "PERFORMANCE DASHBOARD",
    report_subtitle: "",
    period: {
      date: "2026-08-18",
      hours: ["22:00", "23:00", "00:00"],
      current_hour: "00:00"
    },
    tech_5g: {
      gauges: {
        throughput_dl: { label: "Taxa de Dados Direto", value: 15.38, unit: "Mbps", min: 0, max: 100 },
        throughput_ul: { label: "Taxa de Dados Reverso", value: 0.14, unit: "Mbps", min: 0, max: 20 },
        prb_dl: { label: "PRB DL", value: 17.91, unit: "%", min: 0, max: 100 },
        prb_ul: { label: "PRB UL", value: 51.81, unit: "%", min: 0, max: 100 },
        interference: { label: "Interferência", value: -107.67, unit: "dBm", min: -125, max: -85 }
      },
      charts: [
        { id: "users_nsa", title: "Usuários NSA", values: ["2090", "1597", "1284"], raw_values: [2090, 1597, 1284] },
        { id: "volume_dl", title: "Volume Direto\n(GB)", values: ["86.1", "62.0", "42.8"], raw_values: [86.1, 62.0, 42.8] },
        { id: "volume_ul", title: "Volume Reverso\n(GB)", values: ["25.6", "23.6", "23.4"], raw_values: [25.6, 23.6, 23.4] },
        { id: "users_sa", title: "Usuários SA", values: ["1726", "1245", "971"], raw_values: [1726, 1245, 971] }
      ],
      metrics_stack: [
        { label: "Reassembly Fail Rate", value: "0 %", status: "ok" },
        { label: "TX Drop", value: "0 %", status: "ok" },
        { label: "Delta VQI", value: "0.99 %", status: "ok" },
        { label: "Frag. TX Packets", value: "1.54 %", status: "ok" }
      ],
      health_cards: [
        { label: "Acessibilidade NSA", value: "83.73 %", status: "bad" },
        { label: "Drop NSA", value: "0.34 %", status: "good" },
        { label: "Intra-SgNB Pscell", value: "93.32 %", status: "bad" },
        { label: "Inter-SgNB Pscell", value: "91.92 %", status: "bad" },
        { label: "Availability", value: "100 %", status: "good" },
        { label: "Transmission", value: "2.11 %", status: "good" }
      ]
    },
    tech_4g: {
      gauges: {
        throughput_dl: { label: "Taxa de Dados Direto", value: 0.10, unit: "Mbps", min: 0, max: 50 },
        throughput_ul: { label: "Taxa de Dados Reverso", value: 3.05, unit: "Mbps", min: 0, max: 20 },
        prb_dl: { label: "PRB DL", value: 25.15, unit: "%", min: 0, max: 100 },
        prb_ul: { label: "PRB UL", value: 30.63, unit: "%", min: 0, max: 100 },
        interference: { label: "Interferência", value: -105.48, unit: "dBm", min: -125, max: -85 }
      },
      charts: [
        { id: "users_4g", title: "Usuários", values: ["1543", "2139", "2401"], raw_values: [1543, 2139, 2401] },
        { id: "volume_dl_4g", title: "Volume Direto\n(GB)", values: ["34.0", "24.0", "15.4"], raw_values: [34.0, 24.0, 15.4] },
        { id: "volume_ul_4g", title: "Volume Reverso\n(GB)", values: ["26.8", "21.5", "14.6"], raw_values: [26.8, 21.5, 14.6] }
      ],
      volte_table: [
        { label: "Efic ERAB QCI1 (%)", value: "91.6", status: "bad" },
        { label: "Efic ERAB QCI5 (%)", value: "67.7", status: "bad" },
        { label: "Efic SRVCC (%)", value: "—", status: "neutral" },
        { label: "Efic HO VOIP (%)", value: "98.8", status: "good" },
        { label: "Drop ERAB QCI1 (%)", value: "1", status: "bad" },
        { label: "Drop ERAB QCI5 (%)", value: "4.4", status: "bad" },
        { label: "UE Ativo QCI1", value: "0.77", status: "good" },
        { label: "Taxa Dados DL QCI1 (kbps)", value: "18.9", status: "good" },
        { label: "Taxa Dados UL QCI1 (kbps)", value: "484.7", status: "good" }
      ],
      health_cards: [
        { label: "Acessibilidade", value: "63.79 %", status: "bad" },
        { label: "Drop Dados", value: "4.35 %", status: "bad" },
        { label: "Handover", value: "98.18 %", status: "good" },
        { label: "CSFB", value: "96.83 %", status: "bad" },
        { label: "Availability", value: "100 %", status: "good" },
        { label: "Transmission", value: "2.11 %", status: "good" }
      ]
    }
  },

  // 5. Stock Car (Referência 57B7EC37...)
  stock_car: {
    event_name: "STOCK CAR PRO SERIES",
    report_title: "PERFORMANCE DASHBOARD",
    report_subtitle: "",
    period: {
      date: "2026-08-18",
      hours: ["15:00", "16:00", "17:00"],
      current_hour: "17:00"
    },
    tech_5g: {
      gauges: {
        throughput_dl: { label: "Taxa de Dados Direto", value: 294.41, unit: "Mbps", min: 0, max: 400 },
        throughput_ul: { label: "Taxa de Dados Reverso", value: 8.49, unit: "Mbps", min: 0, max: 40 },
        prb_dl: { label: "PRB DL", value: 2.71, unit: "%", min: 0, max: 100 },
        prb_ul: { label: "PRB UL", value: 5.27, unit: "%", min: 0, max: 100 },
        interference: { label: "Interferência", value: -115.50, unit: "dBm", min: -125, max: -85 }
      },
      charts: [
        { id: "users_nsa", title: "Usuários NSA", values: ["47.0", "37.0", "23.6"], raw_values: [47.0, 37.0, 23.6] },
        { id: "users_sa", title: "Usuários SA", values: ["12.9", "11.0", "8.85"], raw_values: [12.9, 11.0, 8.85] },
        { id: "volume_dl", title: "Volume Direto\n(GB)", values: ["8.05", "8.09", "3.77"], raw_values: [8.05, 8.09, 3.77] },
        { id: "volume_ul", title: "Volume Reverso\n(GB)", values: ["5.85", "3.10", "0.42"], raw_values: [5.85, 3.10, 0.42] }
      ],
      metrics_stack: [
        { label: "Reassembly Fail Rate", value: "0 %", status: "ok" },
        { label: "TX Drop", value: "0 %", status: "ok" },
        { label: "Delta VQI", value: "0 %", status: "ok" },
        { label: "Frag. TX Packets", value: "0.04 %", status: "ok" }
      ],
      health_cards: [
        { label: "Acessibilidade NSA", value: "99.72 %", status: "good" },
        { label: "Drop NSA", value: "0 %", status: "good" },
        { label: "Intra-SgNB Pscell", value: "100 %", status: "good" },
        { label: "Inter-SgNB Pscell", value: "98.41 %", status: "good" },
        { label: "Availability", value: "100 %", status: "good" },
        { label: "Transmission", value: "0 %", status: "good" }
      ]
    },
    tech_4g: {
      gauges: {
        throughput_dl: { label: "Taxa de Dados Direto", value: 48.36, unit: "Mbps", min: 0, max: 100 },
        throughput_ul: { label: "Taxa de Dados Reverso", value: 5.43, unit: "Mbps", min: 0, max: 30 },
        prb_dl: { label: "PRB DL", value: 2.39, unit: "%", min: 0, max: 100 },
        prb_ul: { label: "PRB UL", value: 6.49, unit: "%", min: 0, max: 100 },
        interference: { label: "Interferência", value: -118.00, unit: "dBm", min: -125, max: -85 }
      },
      charts: [
        { id: "users_4g", title: "Usuários", values: ["166", "121", "83.7"], raw_values: [166, 121, 83.7] },
        { id: "volume_dl_4g", title: "Volume Direto\n(GB)", values: ["9.73", "7.50", "4.73"], raw_values: [9.73, 7.50, 4.73] },
        { id: "volume_ul_4g", title: "Volume Reverso\n(GB)", values: ["6.53", "3.36", "1.41"], raw_values: [6.53, 3.36, 1.41] }
      ],
      volte_table: [
        { label: "Efic ERAB QCI1 (%)", value: "100", status: "good" },
        { label: "Efic ERAB QCI5 (%)", value: "99.9", status: "good" },
        { label: "Efic SRVCC (%)", value: "—", status: "neutral" },
        { label: "Efic HO VOIP (%)", value: "100", status: "good" },
        { label: "Drop ERAB QCI1 (%)", value: "0", status: "good" },
        { label: "Drop ERAB QCI5 (%)", value: "0.1", status: "good" },
        { label: "UE Ativo QCI1", value: "0.04", status: "good" },
        { label: "Taxa Dados DL QCI1 (kbps)", value: "1050.9", status: "good" },
        { label: "Taxa Dados UL QCI1 (kbps)", value: "420.8", status: "good" }
      ],
      health_cards: [
        { label: "Acessibilidade", value: "99.87 %", status: "good" },
        { label: "Drop Dados", value: "0.27 %", status: "good" },
        { label: "Handover", value: "98.73 %", status: "good" },
        { label: "CSFB", value: "100 %", status: "good" },
        { label: "Availability", value: "100 %", status: "good" },
        { label: "Transmission", value: "3.52 %", status: "good" }
      ]
    }
  },

  // 6. João Rock (Referência 731221E1...)
  joao_rock: {
    event_name: "JOÃO ROCK 2026",
    report_title: "PERFORMANCE DASHBOARD",
    report_subtitle: "",
    period: {
      date: "2026-08-18",
      hours: ["00:00", "01:00", "02:00"],
      current_hour: "02:00"
    },
    tech_5g: {
      gauges: {
        throughput_dl: { label: "Taxa de Dados Direto", value: 215.48, unit: "Mbps", min: 0, max: 300 },
        throughput_ul: { label: "Taxa de Dados Reverso", value: 19.89, unit: "Mbps", min: 0, max: 40 },
        prb_dl: { label: "PRB DL", value: 15.16, unit: "%", min: 0, max: 100 },
        prb_ul: { label: "PRB UL", value: 46.70, unit: "%", min: 0, max: 100 },
        interference: { label: "Interferência", value: -100.83, unit: "dBm", min: -125, max: -85 }
      },
      charts: [
        { id: "users_nsa", title: "Usuários NSA", values: ["2.45k", "2.14k", "1.31k"], raw_values: [2450, 2140, 1310] },
        { id: "volume_dl", title: "Volume Direto\n(GB)", values: ["273", "241", "175"], raw_values: [273, 241, 175] },
        { id: "volume_ul", title: "Volume Reverso\n(GB)", values: ["146", "126", "77.5"], raw_values: [146, 126, 77.5] },
        { id: "users_sa", title: "Usuários SA", values: ["632", "464", "249"], raw_values: [632, 464, 249] }
      ],
      metrics_stack: [
        { label: "Reassembly Fail Rate", value: "0.01 %", status: "ok" },
        { label: "TX Drop", value: "0 %", status: "ok" },
        { label: "Delta VQI", value: "0 %", status: "ok" },
        { label: "Frag. TX Packets", value: "2.13 %", status: "ok" }
      ],
      health_cards: [
        { label: "Acessibilidade NSA", value: "99.75 %", status: "good" },
        { label: "Drop NSA", value: "0.01 %", status: "good" },
        { label: "Intra-SgNB Pscell", value: "99.91 %", status: "good" },
        { label: "Inter-SgNB Pscell", value: "99.62 %", status: "good" },
        { label: "Availability", value: "100 %", status: "good" },
        { label: "Transmission", value: "0 %", status: "good" }
      ]
    },
    tech_4g: {
      gauges: {
        throughput_dl: { label: "Taxa de Dados Direto", value: 27.27, unit: "Mbps", min: 0, max: 100 },
        throughput_ul: { label: "Taxa de Dados Reverso", value: 15.17, unit: "Mbps", min: 0, max: 30 },
        prb_dl: { label: "PRB DL", value: 10.82, unit: "%", min: 0, max: 100 },
        prb_ul: { label: "PRB UL", value: 22.84, unit: "%", min: 0, max: 100 },
        interference: { label: "Interferência", value: -110.56, unit: "dBm", min: -125, max: -85 }
      },
      charts: [
        { id: "users_4g", title: "Usuários", values: ["8.74k", "6.93k", "4.59k"], raw_values: [8740, 6930, 4590] },
        { id: "volume_dl_4g", title: "Volume Direto\n(GB)", values: ["293", "225", "141"], raw_values: [293, 225, 141] },
        { id: "volume_ul_4g", title: "Volume Reverso\n(GB)", values: ["240", "211", "129"], raw_values: [240, 211, 129] }
      ],
      volte_table: [
        { label: "Efic ERAB QCI1 (%)", value: "99.9", status: "good" },
        { label: "Efic ERAB QCI5 (%)", value: "99.7", status: "good" },
        { label: "Efic SRVCC (%)", value: "—", status: "neutral" },
        { label: "Efic HO VOIP (%)", value: "100", status: "good" },
        { label: "Drop ERAB QCI1 (%)", value: "0", status: "good" },
        { label: "Drop ERAB QCI5 (%)", value: "0", status: "good" },
        { label: "UE Ativo QCI1", value: "0.52", status: "good" },
        { label: "Taxa Dados DL QCI1 (kbps)", value: "176.3", status: "good" },
        { label: "Taxa Dados UL QCI1 (kbps)", value: "392.4", status: "good" }
      ],
      health_cards: [
        { label: "Acessibilidade", value: "99.65 %", status: "good" },
        { label: "Drop Dados", value: "0.05 %", status: "good" },
        { label: "Handover", value: "99.7 %", status: "good" },
        { label: "CSFB", value: "100 %", status: "good" },
        { label: "Availability", value: "92.98 %", status: "bad" },
        { label: "Transmission", value: "0 %", status: "good" }
      ]
    }
  }
};

// Export for node or browser
if (typeof module !== "undefined" && module.exports) {
  module.exports = { REPORT_DATASETS };
}
