import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const baseDir = path.resolve(".");
const inputDir = path.join(baseDir, "input");
const previewDir = path.join(baseDir, "previews");

const inputs = [
  "SItes_Aeroporto_TIM_Santaxlsx.xlsx",
  "export_base_4g_huawei_tsl_20260810.csv",
  "site_list_Homero_RoadShow.csv",
];

function decodeCsv(buffer) {
  for (const encoding of ["utf-8", "windows-1252", "utf-16le"]) {
    try {
      return new TextDecoder(encoding, { fatal: encoding === "utf-8" }).decode(buffer);
    } catch {
      // Try the next common export encoding.
    }
  }
  return new TextDecoder("windows-1252").decode(buffer);
}

function columnName(index) {
  let value = index + 1;
  let name = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    name = String.fromCharCode(65 + remainder) + name;
    value = Math.floor((value - 1) / 26);
  }
  return name;
}

async function importWorkbook(filePath) {
  if (filePath.toLowerCase().endsWith(".xlsx")) {
    return SpreadsheetFile.importXlsx(await FileBlob.load(filePath));
  }
  const raw = await fs.readFile(filePath);
  return Workbook.fromCSV(decodeCsv(raw), { sheetName: "Dados" });
}

async function inspectInputs() {
  await fs.mkdir(previewDir, { recursive: true });
  for (const fileName of inputs) {
    const filePath = path.join(inputDir, fileName);
    const workbook = await importWorkbook(filePath);
    const sheetInfo = await workbook.inspect({ kind: "sheet", include: "id,name", maxChars: 4000 });
    const sheet = workbook.worksheets.getItemAt(0);
    const used = sheet.getUsedRange(true);
    const values = used?.values ?? [];
    const rows = values.length;
    const cols = values.reduce((max, row) => Math.max(max, row?.length ?? 0), 0);
    const header = values[0] ?? [];
    const sample = values.slice(0, 6);
    const renderCols = Math.min(cols, 14);
    const renderRows = Math.min(rows, 35);
    if (renderCols && renderRows) {
      const range = `A1:${columnName(renderCols - 1)}${renderRows}`;
      const preview = await workbook.render({ sheetName: sheet.name, range, scale: 1, format: "png" });
      await fs.writeFile(
        path.join(previewDir, `${path.parse(fileName).name}.png`),
        new Uint8Array(await preview.arrayBuffer()),
      );
    }
    console.log(JSON.stringify({ fileName, sheetInfo: sheetInfo.ndjson, rows, cols, header, sample }));
  }
}

const canonicalByFile = {
  "SItes_Aeroporto_TIM_Santaxlsx.xlsx": {
    enodebid: "enodebid",
    cellid: "cellid",
    nename: "nename",
    cellname: "cellname",
    latitude: "latitude",
    longitude: "longitude",
    azimuth: null,
  },
  "export_base_4g_huawei_tsl_20260810.csv": {
    enodebid: "enodebid",
    cellid: "cellid",
    nename: "nename",
    cellname: "cellname",
    latitude: "latitude",
    longitude: "longitude",
    azimuth: null,
  },
  "site_list_Homero_RoadShow.csv": {
    enodebid: "eNodeB ID",
    cellid: "Cell ID",
    nename: "Logical Site",
    cellname: "Cell Name",
    latitude: "Latitude",
    longitude: "Longitude",
    azimuth: "Azimuth",
  },
};

function isBlank(value) {
  return value === null || value === undefined || String(value).trim() === "" || String(value).trim() === "-";
}

function numeric(value) {
  if (isBlank(value)) return null;
  const parsed = Number(String(value).replace(",", "."));
  return Number.isFinite(parsed) ? parsed : null;
}

function wktCoordinates(value) {
  const match = String(value ?? "").match(/Point\s*\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)/i);
  return match ? { longitude: Number(match[1]), latitude: Number(match[2]) } : null;
}

function valueCounts(values, limit = 12) {
  const counts = new Map();
  for (const value of values) {
    const key = isBlank(value) ? "(vazio)" : String(value).trim();
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, limit);
}

async function analyzeInputs() {
  const cellSets = {};
  for (const fileName of inputs) {
    const workbook = await importWorkbook(path.join(inputDir, fileName));
    const sheet = workbook.worksheets.getItemAt(0);
    const values = sheet.getUsedRange(true)?.values ?? [];
    const headers = values[0] ?? [];
    const index = new Map(headers.map((header, i) => [String(header), i]));
    const mapping = canonicalByFile[fileName];
    const sourceRows = values.slice(1).filter((row) => row.some((value) => !isBlank(value)));
    const canonicalRows = sourceRows.map((row) => {
      const result = {};
      for (const [target, source] of Object.entries(mapping)) {
        result[target] = source === null ? null : row[index.get(source)];
      }
      if (fileName === "SItes_Aeroporto_TIM_Santaxlsx.xlsx") {
        const coords = wktCoordinates(row[index.get("wkt_geom")]);
        if (coords) {
          result.latitude = coords.latitude;
          result.longitude = coords.longitude;
        }
      }
      return result;
    });
    const missing = Object.fromEntries(Object.keys(mapping).map((key) => [key, canonicalRows.filter((row) => isBlank(row[key])).length]));
    const latitudes = canonicalRows.map((row) => numeric(row.latitude)).filter((value) => value !== null);
    const longitudes = canonicalRows.map((row) => numeric(row.longitude)).filter((value) => value !== null);
    const azimuths = canonicalRows.map((row) => numeric(row.azimuth)).filter((value) => value !== null);
    const validCoords = canonicalRows.filter((row) => {
      const lat = numeric(row.latitude);
      const lon = numeric(row.longitude);
      return lat !== null && lon !== null && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180;
    }).length;
    const keys = canonicalRows.map((row) => `${String(row.enodebid).trim()}|${String(row.cellname).trim()}`);
    const uniqueKeys = new Set(keys);
    const cellNames = new Set(canonicalRows.map((row) => String(row.cellname ?? "").trim()).filter(Boolean));
    cellSets[fileName] = cellNames;
    const municipalityHeader = index.has("municipio") ? "municipio" : index.has("City") ? "City" : null;
    const regionHeader = index.has("regional") ? "regional" : index.has("UF") ? "UF" : null;
    console.log(JSON.stringify({
      fileName,
      rows: canonicalRows.length,
      uniqueSites: new Set(canonicalRows.map((row) => String(row.enodebid ?? "").trim()).filter(Boolean)).size,
      uniqueCells: cellNames.size,
      duplicateSiteCellRows: canonicalRows.length - uniqueKeys.size,
      missing,
      validCoords,
      invalidCoords: canonicalRows.length - validCoords,
      latitudeRange: latitudes.length ? [Math.min(...latitudes), Math.max(...latitudes)] : null,
      longitudeRange: longitudes.length ? [Math.min(...longitudes), Math.max(...longitudes)] : null,
      azimuthRange: azimuths.length ? [Math.min(...azimuths), Math.max(...azimuths)] : null,
      invalidAzimuths: azimuths.filter((value) => value < 0 || value > 360).length,
      topMunicipalities: municipalityHeader ? valueCounts(sourceRows.map((row) => row[index.get(municipalityHeader)])) : [],
      topRegions: regionHeader ? valueCounts(sourceRows.map((row) => row[index.get(regionHeader)])) : [],
    }));
  }
  const overlaps = [];
  for (let i = 0; i < inputs.length; i += 1) {
    for (let j = i + 1; j < inputs.length; j += 1) {
      let count = 0;
      for (const cell of cellSets[inputs[i]]) if (cellSets[inputs[j]].has(cell)) count += 1;
      overlaps.push({ left: inputs[i], right: inputs[j], commonCellNames: count });
    }
  }
  console.log(JSON.stringify({ overlaps }));
}

function asIdentifier(value) {
  if (isBlank(value)) return "";
  if (typeof value === "number" && Number.isInteger(value)) return String(value);
  return String(value).trim();
}

function preferredRowScore(row) {
  let score = 0;
  if (String(row.nename ?? "").startsWith("SR-")) score += 2;
  if (!isBlank(row._sector)) score += 1;
  return score;
}

function deduplicateRows(rows) {
  const selected = new Map();
  for (const row of rows) {
    const key = `${row.enodebid}|${row.cellname}`;
    const current = selected.get(key);
    if (!current || preferredRowScore(row) > preferredRowScore(current)) selected.set(key, row);
  }
  return [...selected.values()];
}

async function sourceTable(fileName) {
  const workbook = await importWorkbook(path.join(inputDir, fileName));
  const values = workbook.worksheets.getItemAt(0).getUsedRange(true)?.values ?? [];
  const headers = values[0] ?? [];
  return {
    headers,
    index: new Map(headers.map((header, i) => [String(header), i])),
    rows: values.slice(1).filter((row) => row.some((value) => !isBlank(value))),
  };
}

function canonicalize(fileName, table, fallbackCellNames = new Map()) {
  const mapping = canonicalByFile[fileName];
  const rows = table.rows.map((sourceRow) => {
    const row = {};
    for (const [target, source] of Object.entries(mapping)) {
      row[target] = source === null ? null : sourceRow[table.index.get(source)];
    }
    if (fileName === "SItes_Aeroporto_TIM_Santaxlsx.xlsx") {
      const coords = wktCoordinates(sourceRow[table.index.get("wkt_geom")]);
      if (coords) {
        row.latitude = coords.latitude;
        row.longitude = coords.longitude;
      }
    }
    row.enodebid = asIdentifier(row.enodebid);
    row.cellid = asIdentifier(row.cellid);
    row.nename = asIdentifier(row.nename);
    row.cellname = asIdentifier(row.cellname);
    row.latitude = numeric(row.latitude);
    row.longitude = numeric(row.longitude);
    row.azimuth = numeric(row.azimuth);
    row._sector = table.index.has("sector") ? sourceRow[table.index.get("sector")] : table.index.has("Sector") ? sourceRow[table.index.get("Sector")] : null;
    if (!row.cellname) row.cellname = fallbackCellNames.get(`${row.enodebid}|${row.cellid}`) ?? "";
    return row;
  });
  const validIdentityRows = rows.filter((row) => row.enodebid && row.cellid && row.nename && row.cellname);
  return deduplicateRows(validIdentityRows);
}

async function writeEpWorkbook(outputPath, rows, { incompleteAzimuth = false } = {}) {
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add("EP");
  const headers = ["enodebid", "cellid", "nename", "cellname", "latitude", "longitude", "azimuth"];
  const matrix = [
    headers,
    ...rows.map((row) => [
      row.enodebid,
      row.cellid,
      row.nename,
      row.cellname,
      row.latitude,
      row.longitude,
      row.azimuth,
    ]),
  ];
  sheet.getRangeByIndexes(0, 0, matrix.length, headers.length).values = matrix;
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  const headerRange = sheet.getRange("A1:G1");
  headerRange.format = {
    fill: "#17365D",
    font: { bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  headerRange.format.rowHeight = 26;
  const lastRow = rows.length + 1;
  sheet.getRange(`A2:D${lastRow}`).format.numberFormat = "@";
  sheet.getRange(`E2:F${lastRow}`).format.numberFormat = "0.000000";
  sheet.getRange(`G2:G${lastRow}`).format.numberFormat = "0";
  sheet.getRange("A1").format.columnWidth = 14;
  sheet.getRange("B1").format.columnWidth = 11;
  sheet.getRange("C1").format.columnWidth = 22;
  sheet.getRange("D1").format.columnWidth = 28;
  sheet.getRange("E1").format.columnWidth = 14;
  sheet.getRange("F1").format.columnWidth = 14;
  sheet.getRange("G1").format.columnWidth = 11;
  if (incompleteAzimuth && rows.length) {
    sheet.getRange(`G2:G${lastRow}`).format.fill = "#FFF2CC";
  }
  const inspect = await workbook.inspect({
    kind: "table",
    range: `EP!A1:G${Math.min(lastRow, 8)}`,
    include: "values,formulas",
    tableMaxRows: 8,
    tableMaxCols: 7,
    maxChars: 5000,
  });
  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 50 },
    summary: "final formula error scan",
    maxChars: 3000,
  });
  const preview = await workbook.render({
    sheetName: "EP",
    range: `A1:G${Math.min(lastRow, 35)}`,
    scale: 1.5,
    format: "png",
  });
  const previewPath = path.join(previewDir, `${path.parse(outputPath).name}_final.png`);
  await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(outputPath);
  return { outputPath, previewPath, rows: rows.length, inspect: inspect.ndjson, errors: errors.ndjson };
}

async function buildOutputs() {
  await fs.mkdir(previewDir, { recursive: true });
  const source = {};
  for (const fileName of inputs) source[fileName] = await sourceTable(fileName);

  const baseName = "export_base_4g_huawei_tsl_20260810.csv";
  const baseRows = canonicalize(baseName, source[baseName]);
  const fallbackCellNames = new Map(baseRows.map((row) => [`${row.enodebid}|${row.cellid}`, row.cellname]));
  const airportRows = canonicalize("SItes_Aeroporto_TIM_Santaxlsx.xlsx", source["SItes_Aeroporto_TIM_Santaxlsx.xlsx"], fallbackCellNames);
  const roadShowRows = canonicalize("site_list_Homero_RoadShow.csv", source["site_list_Homero_RoadShow.csv"]);

  const results = [];
  results.push(await writeEpWorkbook(
    path.join(baseDir, "SItes_Aeroporto_TIM_Santa_EP_INCOMPLETO_sem_azimute.xlsx"),
    airportRows,
    { incompleteAzimuth: true },
  ));
  results.push(await writeEpWorkbook(
    path.join(baseDir, "export_base_4g_huawei_tsl_20260810_EP_INCOMPLETO_sem_azimute.xlsx"),
    baseRows,
    { incompleteAzimuth: true },
  ));
  results.push(await writeEpWorkbook(
    path.join(baseDir, "site_list_Homero_RoadShow_EP_limpo.xlsx"),
    roadShowRows,
  ));
  for (const result of results) console.log(JSON.stringify(result));
}

async function rebuildLargeOutput() {
  await fs.mkdir(previewDir, { recursive: true });
  const fileName = "export_base_4g_huawei_tsl_20260810.csv";
  const rows = canonicalize(fileName, await sourceTable(fileName));
  const result = await writeEpWorkbook(
    path.join(baseDir, "export_base_4g_huawei_tsl_20260810_EP_INCOMPLETO_sem_azimute.xlsx"),
    rows,
    { incompleteAzimuth: true },
  );
  console.log(JSON.stringify(result));
}

async function analyzeAirportSectors() {
  const fileName = "SItes_Aeroporto_TIM_Santaxlsx.xlsx";
  const table = await sourceTable(fileName);
  const sectorIndex = table.index.get("sector");
  const counts = valueCounts(table.rows.map((row) => row[sectorIndex]), 20);
  console.log(JSON.stringify({ fileName, rows: table.rows.length, sectorCounts: counts }));
}

async function buildEstimatedAirportOutput() {
  await fs.mkdir(previewDir, { recursive: true });
  const airportName = "SItes_Aeroporto_TIM_Santaxlsx.xlsx";
  const airportTable = await sourceTable(airportName);
  const sectorIndex = airportTable.index.get("sector");
  const sourceEnodebIndex = airportTable.index.get("enodebid");
  const sourceCellIndex = airportTable.index.get("cellid");
  const sectorByKey = new Map(airportTable.rows.map((sourceRow) => [
    `${asIdentifier(sourceRow[sourceEnodebIndex])}|${asIdentifier(sourceRow[sourceCellIndex])}`,
    sourceRow[sectorIndex],
  ]));
  const cleanPath = path.join(baseDir, "SItes_Aeroporto_TIM_Santa_EP_INCOMPLETO_sem_azimute.xlsx");
  const cleanWorkbook = await SpreadsheetFile.importXlsx(await FileBlob.load(cleanPath));
  const cleanValues = cleanWorkbook.worksheets.getItemAt(0).getUsedRange(true)?.values ?? [];
  const rows = cleanValues.slice(1).map((values) => ({
    enodebid: asIdentifier(values[0]),
    cellid: asIdentifier(values[1]),
    nename: asIdentifier(values[2]),
    cellname: asIdentifier(values[3]),
    latitude: numeric(values[4]),
    longitude: numeric(values[5]),
    azimuth: null,
    _sector: sectorByKey.get(`${asIdentifier(values[0])}|${asIdentifier(values[1])}`),
  }));
  const sectorsBySite = new Map();
  for (const row of rows) {
    const sector = numeric(row._sector);
    if (sector === null) continue;
    if (!sectorsBySite.has(row.enodebid)) sectorsBySite.set(row.enodebid, new Set());
    sectorsBySite.get(row.enodebid).add(sector);
  }
  const approximateAzimuthBySite = new Map();
  for (const [siteId, sectorSet] of sectorsBySite.entries()) {
    const sectors = [...sectorSet].sort((a, b) => a - b);
    const mapping = new Map(sectors.map((sector, index) => [sector, Math.round((360 * index) / sectors.length)]));
    approximateAzimuthBySite.set(siteId, mapping);
  }
  let unmapped = 0;
  for (const row of rows) {
    const sector = numeric(row._sector);
    const approximateAzimuth = approximateAzimuthBySite.get(row.enodebid)?.get(sector);
    if (approximateAzimuth === undefined) {
      unmapped += 1;
      row.azimuth = 0;
    } else {
      row.azimuth = approximateAzimuth;
    }
  }
  const outputPath = path.join(baseDir, "SItes_Aeroporto_TIM_Santa_EP_azimute_estimado.xlsx");
  const result = await writeEpWorkbook(outputPath, rows, { incompleteAzimuth: false });
  console.log(JSON.stringify({ ...result, unmapped }));
}

async function verifyOutputs() {
  const outputs = [
    "SItes_Aeroporto_TIM_Santa_EP_INCOMPLETO_sem_azimute.xlsx",
    "export_base_4g_huawei_tsl_20260810_EP_INCOMPLETO_sem_azimute.xlsx",
    "site_list_Homero_RoadShow_EP_limpo.xlsx",
  ];
  for (const fileName of outputs) {
    const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(path.join(baseDir, fileName)));
    const sheet = workbook.worksheets.getItemAt(0);
    const values = sheet.getRange("A1:G8").values;
    const styles = await workbook.inspect({
      kind: "computedStyle",
      sheetId: sheet.name,
      range: "A1:G3",
      maxChars: 4000,
    });
    const errors = await workbook.inspect({
      kind: "match",
      searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
      options: { useRegex: true, maxResults: 50 },
      summary: "verification formula error scan",
      maxChars: 3000,
    });
    const preview = await workbook.render({ sheetName: sheet.name, range: "A1:G35", scale: 1, format: "png" });
    const verifyPreview = path.join(previewDir, `${path.parse(fileName).name}_verify.png`);
    await fs.writeFile(verifyPreview, new Uint8Array(await preview.arrayBuffer()));
    console.log(JSON.stringify({ fileName, values, styles: styles.ndjson, errors: errors.ndjson, verifyPreview }));
  }
}

async function verifyEstimatedAirportOutput() {
  const fileName = "SItes_Aeroporto_TIM_Santa_EP_azimute_estimado.xlsx";
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(path.join(baseDir, fileName)));
  const sheet = workbook.worksheets.getItemAt(0);
  const values = sheet.getUsedRange(true)?.values ?? [];
  const azimuths = values.slice(1).map((row) => numeric(row[6]));
  const check = await workbook.inspect({
    kind: "table",
    range: "EP!A1:G12",
    include: "values,formulas",
    tableMaxRows: 12,
    tableMaxCols: 7,
    maxChars: 6000,
  });
  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 50 },
    summary: "final formula error scan",
    maxChars: 3000,
  });
  const preview = await workbook.render({ sheetName: "EP", range: "A1:G35", scale: 1.5, format: "png" });
  const previewPath = path.join(previewDir, "SItes_Aeroporto_TIM_Santa_EP_azimute_estimado_verify.png");
  await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
  console.log(JSON.stringify({
    fileName,
    rows: values.length - 1,
    blankAzimuths: azimuths.filter((value) => value === null).length,
    invalidAzimuths: azimuths.filter((value) => value !== null && (value < 0 || value >= 360)).length,
    azimuthCounts: valueCounts(azimuths),
    check: check.ndjson,
    errors: errors.ndjson,
    previewPath,
  }));
}

if (process.argv[2] === "inspect") {
  await inspectInputs();
} else if (process.argv[2] === "analyze") {
  await analyzeInputs();
} else if (process.argv[2] === "build") {
  await buildOutputs();
} else if (process.argv[2] === "build-large") {
  await rebuildLargeOutput();
} else if (process.argv[2] === "analyze-airport-sectors") {
  await analyzeAirportSectors();
} else if (process.argv[2] === "build-estimated-airport") {
  await buildEstimatedAirportOutput();
} else if (process.argv[2] === "verify") {
  await verifyOutputs();
} else if (process.argv[2] === "verify-estimated-airport") {
  await verifyEstimatedAirportOutput();
} else {
  throw new Error("Modo esperado: inspect, analyze, build, build-large, analyze-airport-sectors, build-estimated-airport, verify ou verify-estimated-airport");
}
