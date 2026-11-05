// Part editor (owner and manager). A plain table: the server validates every
// formula against every size when saving, and its message is shown as-is.
const itemId = location.pathname.split("/").pop();
const $ = (id) => document.getElementById(id);
const HEADERS = { "Content-Type": "application/json", "X-Lab-Client": "1" };

const FIELDS = [
  ["name", "name", "text"],
  ["wood_type", "wood", "text"],
  ["l_expr", "H - 30", "text"],
  ["w_expr", "40", "text"],
  ["t_expr", "40", "text"],
  ["axis", null, "axis"],
  ["x_expr", "0", "text"],
  ["y_expr", "0", "text"],
  ["z_expr", "0", "text"],
  ["assembly_step", null, "number"],
  ["joint_type", null, "joint"],
  ["joinery_note", "", "text"],
];

const JOINTS = [
  ["", "none set"],
  ["butt_glue", "Butt joint, glued"],
  ["mortise_tenon", "Mortise and tenon"],
  ["dowel", "Dowel joint"],
  ["half_lap", "Half-lap"],
  ["dovetail", "Dovetail"],
  ["pocket_screw", "Pocket screw"],
  ["biscuit", "Biscuit joint"],
  ["other", "Other (use the note)"],
];
const KINDS = [
  ["screw", "Screw"],
  ["nail", "Nail"],
  ["dowel", "Dowel"],
  ["bolt", "Bolt"],
  ["bracket", "Bracket"],
  ["glue", "Glue"],
  ["clamp", "Clamp (workshop tool, not stock)"],
  ["other", "Other"],
];

function select(options, value) {
  const input = document.createElement("select");
  for (const [v, label] of options) {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = label;
    input.append(o);
  }
  input.value = value || options[0][0];
  return input;
}

function cell(field, hint, kind, value) {
  const td = document.createElement("td");
  if (field === "name") td.className = "name";
  if (field === "joinery_note") td.className = "note";
  let input;
  if (kind === "axis") {
    input = select([["x", "x (left-right)"], ["y", "y (up)"], ["z", "z (front-back)"]], value);
  } else if (kind === "joint") {
    input = select(JOINTS, value);
  } else {
    input = document.createElement("input");
    input.type = kind === "number" ? "number" : "text";
    if (kind === "number") {
      input.min = "1";
      input.max = "50";
      input.style.minWidth = "56px";
    }
    input.value = value ?? (kind === "number" ? 1 : "");
    input.placeholder = hint || "";
    input.maxLength = field === "name" ? 80 : field === "joinery_note" ? 300 : 100;
  }
  input.dataset.field = field;
  input.setAttribute("aria-label", field.replace("_expr", "").replace("_", " "));
  td.append(input);
  return td;
}

function addRow(part = {}) {
  const tr = document.createElement("tr");
  tr.dataset.partId = part.id || "";
  tr.dataset.raw = part.raw_material_id || "";
  tr.dataset.model = part.model_asset_id || "";
  const defaults = { x_expr: "0", y_expr: "0", z_expr: "0", axis: "x", assembly_step: 1 };
  for (const [field, hint, kind] of FIELDS) tr.append(cell(field, hint, kind, part[field] ?? defaults[field]));
  const td = document.createElement("td");
  td.style.whiteSpace = "nowrap";
  const copy = document.createElement("button");
  copy.type = "button";
  copy.textContent = "Copy";
  copy.addEventListener("click", () => {
    const part = readRow(tr);
    delete part.id; // a copy is a new part
    tr.after(rowFrom(part));
  });
  const del = document.createElement("button");
  del.type = "button";
  del.className = "link";
  del.textContent = "Delete";
  del.addEventListener("click", () => tr.remove());
  td.append(copy, del);
  tr.append(td);
  return tr;
}
const rowFrom = (part) => addRow(part);

function readRow(tr) {
  const part = {};
  for (const el of tr.querySelectorAll("[data-field]")) {
    let v = el.value.trim();
    if (el.dataset.field === "assembly_step") v = Number(v) || 1;
    part[el.dataset.field] = v === "" && ["wood_type", "joint_type", "joinery_note"].includes(el.dataset.field) ? null : v;
  }
  if (tr.dataset.partId) part.id = tr.dataset.partId; // keeps hardware linked to this part
  part.raw_material_id = tr.dataset.raw || null;
  part.model_asset_id = tr.dataset.model || null;
  return part;
}

async function call(method, path, body) {
  const form = body instanceof FormData; // a photo upload: the browser sets the content type
  const res = await fetch(path, {
    method,
    headers: form ? { "X-Lab-Client": "1" } : HEADERS,
    credentials: "same-origin",
    body: body ? (form ? body : JSON.stringify(body)) : undefined,
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = json.detail;
    const msg = typeof d === "string" ? d : Array.isArray(d) ? d.map((e) => `${(e.loc || []).slice(-1)}: ${e.msg}`).join("; ") : "Could not save.";
    throw new Error(res.status === 401 || res.status === 403 ? "Open the editor from the app with an owner or manager login." : msg);
  }
  return json;
}

// ---------------------------------------------------------------- sizes
const SIZE_ROWS = [["dim", "Standard"], ["S", "Small"], ["M", "Medium"], ["L", "Large"]];
function sizeInputs() {
  const body = $("sizes").tBodies[0];
  body.replaceChildren();
  for (const [key, label] of SIZE_ROWS) {
    const tr = body.insertRow();
    tr.dataset.key = key;
    tr.insertCell().textContent = label;
    for (const axis of ["l", "w", "h"]) {
      const td = tr.insertCell();
      const i = document.createElement("input");
      i.type = "number";
      i.min = "50";
      i.max = "5000";
      i.dataset.axis = axis;
      i.setAttribute("aria-label", `${label} ${axis.toUpperCase()}`);
      td.append(i);
    }
  }
}
function fillSizes(def) {
  const set = (key, v) => {
    const tr = $("sizes").querySelector(`tr[data-key="${key}"]`);
    for (const i of tr.querySelectorAll("input")) i.value = v?.[i.dataset.axis] ?? "";
  };
  set("dim", def.dimensions_mm);
  for (const k of ["S", "M", "L"]) set(k, def.presets[k]);
}
function readSize(key) {
  const tr = $("sizes").querySelector(`tr[data-key="${key}"]`);
  const v = {};
  let any = false;
  for (const i of tr.querySelectorAll("input")) {
    if (i.value !== "") any = true;
    v[i.dataset.axis] = Number(i.value);
  }
  if (!any) return null;
  if (!(v.l && v.w && v.h)) throw new Error(`Fill all three numbers for ${key === "dim" ? "the standard size" : "size " + key}, or leave the row empty`);
  return v;
}

$("saveSizes").addEventListener("click", async () => {
  $("sizesError").textContent = "";
  $("sizesOk").textContent = "";
  try {
    const dim = readSize("dim");
    const presets = {};
    for (const k of ["S", "M", "L"]) {
      const v = readSize(k);
      if (v) presets[k] = v;
    }
    await call("PUT", `/lab/items/${itemId}/furniture`, { is_furniture: $("isFurniture").checked });
    if (dim) await call("PUT", `/lab/items/${itemId}/size`, dim);
    await call("PUT", `/lab/items/${itemId}/presets`, presets);
    $("sizesOk").textContent = "Saved";
  } catch (e) {
    $("sizesError").textContent = e.message;
  }
});

// ---------------------------------------------------------------- parts
$("addPart").addEventListener("click", () => {
  const tr = addRow({ name: "New part", l_expr: "L", w_expr: "100", t_expr: "20" });
  $("parts").tBodies[0].append(tr);
  tr.querySelector("input").focus();
});
$("saveParts").addEventListener("click", async () => {
  $("partsError").textContent = "";
  $("partsOk").textContent = "";
  const parts = [...$("parts").tBodies[0].rows].map(readRow);
  try {
    await call("PUT", `/lab/items/${itemId}/parts`, { parts });
    $("partsOk").textContent = `Saved ${parts.length} parts. Open the Lab view to check them.`;
    await syncPartIds(); // new parts now have ids: keep them, so carvings and hardware stay on them
  } catch (e) {
    $("partsError").textContent = e.message;
  }
});

// ---------------------------------------------------------------- hardware
function hwCell(field, td, input) {
  input.dataset.field = field;
  input.setAttribute("aria-label", field === "qty_expr" ? "quantity formula" : field.replace("_", " "));
  td.append(input);
  return td;
}
function textInput(value, hint, max) {
  const i = document.createElement("input");
  i.type = "text";
  i.value = value ?? "";
  i.placeholder = hint;
  i.maxLength = max;
  return i;
}
function addHwRow(line = {}) {
  const tr = document.createElement("tr");
  tr.dataset.partId = line.part_id || ""; // set through the API; kept when saving here
  const name = hwCell("name", document.createElement("td"), textInput(line.name, "Wood screw 4 x 50 mm", 80));
  name.className = "name";
  const note = hwCell("note", document.createElement("td"), textInput(line.note, "", 300));
  note.className = "note";
  const step = document.createElement("input");
  step.type = "number";
  step.min = "1";
  step.max = "50";
  step.style.minWidth = "56px";
  step.value = line.assembly_step ?? "";
  tr.append(
    hwCell("kind", document.createElement("td"), select(KINDS, line.kind)),
    name,
    hwCell("qty_expr", document.createElement("td"), textInput(line.qty_expr, "L / 150", 100)),
    hwCell("assembly_step", document.createElement("td"), step),
    note
  );
  const td = document.createElement("td");
  td.style.whiteSpace = "nowrap";
  const copy = document.createElement("button");
  copy.type = "button";
  copy.textContent = "Copy";
  copy.addEventListener("click", () => tr.after(addHwRow(readHwRow(tr))));
  const del = document.createElement("button");
  del.type = "button";
  del.className = "link";
  del.textContent = "Delete";
  del.addEventListener("click", () => tr.remove());
  td.append(copy, del);
  tr.append(td);
  return tr;
}
function readHwRow(tr) {
  const line = {};
  for (const el of tr.querySelectorAll("[data-field]")) {
    const v = el.value.trim();
    line[el.dataset.field] = el.dataset.field === "assembly_step" ? (v === "" ? null : Number(v)) : v === "" && el.dataset.field === "note" ? null : v;
  }
  line.part_id = tr.dataset.partId || null;
  return line;
}
$("addHw").addEventListener("click", () => {
  const tr = addHwRow({ kind: "screw", name: "New hardware", qty_expr: "4" });
  $("hardware").tBodies[0].append(tr);
  tr.querySelector("select").focus();
});
$("saveHw").addEventListener("click", async () => {
  $("hwError").textContent = "";
  $("hwOk").textContent = "";
  const hardware = [...$("hardware").tBodies[0].rows].map(readHwRow);
  try {
    await call("PUT", `/lab/items/${itemId}/hardware`, { hardware });
    $("hwOk").textContent = `Saved ${hardware.length} lines. Open the Lab view to check them.`;
  } catch (e) {
    $("hwError").textContent = e.message;
  }
});

// ---------------------------------------------------------------- carvings
const FACE_OPTIONS = [
  ["front", "front (z = 0 side)"],
  ["back", "back"],
  ["left", "left (x = 0 side)"],
  ["right", "right"],
  ["top", "top"],
  ["bottom", "bottom"],
];
let partChoices = []; // saved parts: [id, label]
let designChoices = []; // [id, label, has picture]

function partLabels(parts) {
  const total = {};
  parts.forEach((p) => (total[p.name] = (total[p.name] || 0) + 1));
  const seen = {};
  return parts.map((p) => {
    seen[p.name] = (seen[p.name] || 0) + 1;
    return [p.id, total[p.name] > 1 ? `${p.name} (${seen[p.name]})` : p.name];
  });
}

function fillPartSelect(sel, value) {
  sel.replaceChildren();
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "choose a part";
  sel.append(none);
  for (const [id, label] of partChoices) {
    const o = document.createElement("option");
    o.value = id;
    o.textContent = label;
    sel.append(o);
  }
  sel.value = value || "";
}

function fillDesignSelect(sel, value) {
  sel.replaceChildren();
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "choose a design";
  sel.append(none);
  for (const [id, label, has] of designChoices) {
    const o = document.createElement("option");
    o.value = id;
    o.textContent = has ? label : `${label} (no picture yet)`;
    o.disabled = !has;
    sel.append(o);
  }
  sel.value = value || "";
}

function addCarvingRow(c = {}) {
  const tr = document.createElement("tr");
  const part = document.createElement("select");
  fillPartSelect(part, c.part_id);
  const design = document.createElement("select");
  fillDesignSelect(design, c.design_id);
  const face = select(FACE_OPTIONS, c.face || "front");
  const margin = document.createElement("input");
  margin.type = "number";
  margin.min = "0";
  margin.max = "500";
  margin.style.minWidth = "64px";
  margin.value = c.margin_mm ?? 0;
  const mirror = document.createElement("input");
  mirror.type = "checkbox";
  mirror.checked = !!c.mirror;
  mirror.style.width = "auto";
  const note = textInput(c.note, "", 300);
  const fields = { part_id: part, design_id: design, face, margin_mm: margin, mirror, note };
  for (const [field, input] of Object.entries(fields)) {
    input.dataset.field = field;
    input.setAttribute("aria-label", field === "part_id" ? "part" : field === "design_id" ? "design" : field.replace("_mm", "").replace("_", " "));
    const td = document.createElement("td");
    if (field === "note") td.className = "note";
    td.append(input);
    tr.append(td);
  }
  const td = document.createElement("td");
  td.style.whiteSpace = "nowrap";
  const copy = document.createElement("button");
  copy.type = "button";
  copy.textContent = "Copy";
  copy.addEventListener("click", () => tr.after(addCarvingRow(readCarvingRow(tr))));
  const del = document.createElement("button");
  del.type = "button";
  del.className = "link";
  del.textContent = "Delete";
  del.addEventListener("click", () => tr.remove());
  td.append(copy, del);
  tr.append(td);
  return tr;
}
function readCarvingRow(tr) {
  const v = {};
  for (const el of tr.querySelectorAll("[data-field]")) {
    v[el.dataset.field] = el.type === "checkbox" ? el.checked : el.value.trim();
  }
  return { part_id: v.part_id, design_id: v.design_id, face: v.face, margin_mm: Number(v.margin_mm) || 0, mirror: v.mirror, note: v.note || null };
}
function refreshPartSelects() {
  for (const sel of $("carvings").querySelectorAll("select[data-field=part_id]")) fillPartSelect(sel, sel.value);
}

// ---------------------------------------------------------------- start from a template or another piece
let templates = [];
const hasAnything = () => $("parts").tBodies[0].rows.length + $("hardware").tBodies[0].rows.length + $("carvings").tBodies[0].rows.length > 0;

function showTemplate() {
  const t = templates.find((x) => x.id === $("tplPick").value);
  const info = $("tplInfo");
  info.replaceChildren();
  if (!t) return;
  const add = (text) => {
    const d = document.createElement("div");
    d.textContent = text;
    info.append(d);
  };
  add(`${t.description}`);
  add(`${t.parts} parts, ${t.hardware} hardware lines. Sized for ${t.size_hint.l} × ${t.size_hint.w} × ${t.size_hint.h} mm, and it follows the size you give the piece.`);
  if (t.typical_sizes?.length) add(`Typical sizes (mm): ${t.typical_sizes.map((s) => `${s.name} ${s.l} × ${s.w} × ${s.h}`).join(" · ")}`);
  for (const c of t.carve || []) add(`Carve: ${c}`);
}

async function startFrom(path, body, what) {
  $("startError").textContent = "";
  $("startOk").textContent = "";
  let replace = false;
  if (hasAnything()) {
    if (!confirm(`This piece already has parts, hardware or carvings. Replace them with ${what}? This cannot be undone.`)) return;
    replace = true;
  }
  $("tplUse").disabled = $("cpUse").disabled = true;
  try {
    await call("POST", path, { ...body, replace });
    $("startOk").textContent = "Done. Loading the parts...";
    location.reload(); // show the new parts in the tables
  } catch (e) {
    $("startError").textContent = e.message;
    $("tplUse").disabled = $("cpUse").disabled = false;
  }
}

$("tplPick").addEventListener("change", showTemplate);
$("tplUse").addEventListener("click", () => {
  const t = templates.find((x) => x.id === $("tplPick").value);
  if (!t) return;
  startFrom(`/lab/items/${itemId}/apply-template`, { template: t.id, wood_type: $("tplWood").value.trim() || null }, `the template "${t.name}"`);
});
$("cpUse").addEventListener("click", () => {
  const sel = $("cpPick");
  if (!sel.value) {
    $("startError").textContent = "Choose a piece to copy from.";
    return;
  }
  startFrom(`/lab/items/${itemId}/copy-parts`, { source_item_id: sel.value, hardware: $("cpHw").checked, carvings: $("cpCv").checked }, `a copy of ${sel.selectedOptions[0].textContent}`);
});

// The product's category has a template that fits: pick it, say so, and offer its typical size if the piece has none.
function suggest(sg, hasSize) {
  if (!sg) return;
  $("tplPick").value = sg.template;
  showTemplate();
  const note = document.createElement("div");
  note.textContent = `Suggested for the category "${sg.category}"${sg.size ? `, typical size: ${sg.size.name} ${sg.size.l} × ${sg.size.w} × ${sg.size.h} mm` : ""}.`;
  note.style.fontWeight = "600";
  $("tplInfo").prepend(note);
  if (!hasSize && sg.size) {
    const b = $("tplSize");
    b.hidden = false;
    b.onclick = () => {
      const tr = $("sizes").querySelector('tr[data-key="dim"]');
      for (const i of tr.querySelectorAll("input")) i.value = sg.size[i.dataset.axis];
      $("startOk").textContent = "Typical size filled in. Press Save sizes, then use the template.";
    };
  }
}

async function loadStarts(sg, hasSize) {
  try {
    templates = await call("GET", "/lab/templates");
    const pick = $("tplPick");
    pick.replaceChildren(...templates.map((t) => Object.assign(document.createElement("option"), { value: t.id, textContent: t.name })));
    showTemplate();
    suggest(sg, hasSize);
    const pieces = (await call("GET", "/lab/items")).filter((p) => p.id !== itemId && p.parts > 0);
    const cp = $("cpPick");
    const none = Object.assign(document.createElement("option"), { value: "", textContent: pieces.length ? "choose a piece" : "no other piece has parts yet" });
    cp.replaceChildren(none, ...pieces.map((p) => Object.assign(document.createElement("option"), { value: p.id, textContent: `${p.name} (${p.parts} parts)` })));
  } catch (e) {
    $("startError").textContent = e.message;
  }
}

// ---------------------------------------------------------------- designs from scans
function scanState(s) {
  if (s.status === "uploaded") return "waiting for the workshop PC";
  if (s.status === "processing") return `being read: ${s.progress}%${s.note ? ` (${s.note})` : ""}`;
  return `failed: ${s.error || "no reason given"}`;
}

function scanRow(s) {
  const tr = document.createElement("tr");
  const when = new Date(s.created_at).toLocaleDateString();
  const bits = [s.label || (s.area ? "Marked area" : "Flat carving"), when, s.real_width_mm ? `${s.real_width_mm} mm wide` : "size not set", s.gcode ? "with G-code" : "no G-code"];
  tr.insertCell().textContent = bits.join(" · ");
  const design = tr.insertCell();
  const action = tr.insertCell();
  if (s.status !== "done") {
    design.textContent = scanState(s);
    return tr;
  }
  const show = (d) => {
    design.textContent = `${d.sku} · ${d.name}`;
    action.replaceChildren();
  };
  if (s.design) {
    show(s.design);
    return tr;
  }
  design.textContent = "not a design yet";
  const name = textInput("", "name (optional)", 120);
  name.setAttribute("aria-label", "design name");
  const make = document.createElement("button");
  make.type = "button";
  make.textContent = "Make a design";
  make.addEventListener("click", async () => {
    $("scansError").textContent = "";
    $("scansOk").textContent = "";
    make.disabled = true;
    try {
      const r = await call("POST", `/lab/scans/${s.id}/design`, name.value.trim() ? { name: name.value.trim() } : {});
      show(r.design);
      if (!designChoices.some(([id]) => id === r.design.id)) designChoices.push([r.design.id, `${r.design.sku} · ${r.design.name}`, r.design.has_image]);
      for (const sel of $("carvings").querySelectorAll("select[data-field=design_id]")) fillDesignSelect(sel, sel.value);
      $("scansOk").textContent = r.created ? `Made ${r.design.sku}. You can carve it below.` : `That picture already is ${r.design.sku}.`;
    } catch (e) {
      make.disabled = false;
      $("scansError").textContent = e.message;
    }
  });
  action.style.whiteSpace = "nowrap";
  action.append(name, make);
  return tr;
}

// The list of scans is redrawn only when it changed (so a name being typed is not wiped),
// and refreshed every 10 s while a marked area waits for or is on the workshop PC.
let scansJson = "";
let scanTimer = null;
function renderScans(list) {
  const json = JSON.stringify(list);
  if (json === scansJson) return;
  scansJson = json;
  $("scans").tBodies[0].replaceChildren(...list.map(scanRow));
  $("scans").hidden = !list.length;
  $("noScans").hidden = !!list.length;
  clearInterval(scanTimer);
  if (list.some((s) => s.status === "uploaded" || s.status === "processing")) scanTimer = setInterval(refreshScans, 10000);
}
async function refreshScans() {
  try {
    renderScans(await call("GET", `/lab/items/${itemId}/scans`));
  } catch {
    /* try again at the next tick */
  }
}

// ---------------------------------------------------------------- mark an area on a photo
const CORNER_NAMES = ["top-left", "top-right", "bottom-right", "bottom-left"];
const area = { img: null, file: null, corners: [null, null, null, null], outline: [] };

function areaHelp() {
  const set = area.corners.filter(Boolean).length;
  $("areaHelp").textContent =
    set < 4
      ? `Click the ${CORNER_NAMES[area.corners.findIndex((c) => !c)]} corner (${set + 1} of 4), or type the corners below.`
      : `Corners set. If the shape is not a rectangle, click points around it (${area.outline.length} so far); otherwise press Send.`;
}

function drawArea() {
  const c = $("areaCanvas");
  const ctx = c.getContext("2d");
  const k = c.width / area.img.naturalWidth;
  ctx.drawImage(area.img, 0, 0, c.width, c.height);
  const trace = (pts, colour, closed) => {
    ctx.strokeStyle = colour;
    ctx.lineWidth = 2;
    ctx.beginPath();
    pts.forEach((p, i) => (i ? ctx.lineTo(p.x * k, p.y * k) : ctx.moveTo(p.x * k, p.y * k)));
    if (closed) ctx.closePath();
    ctx.stroke();
  };
  const placed = area.corners.filter(Boolean);
  if (placed.length > 1) trace(placed, "#e02020", placed.length === 4);
  if (area.outline.length > 1) trace(area.outline, "#1f6feb", area.outline.length > 2);
  const dot = (p, colour, text) => {
    ctx.fillStyle = colour;
    ctx.beginPath();
    ctx.arc(p.x * k, p.y * k, text ? 7 : 4, 0, Math.PI * 2);
    ctx.fill();
    if (text) {
      ctx.fillStyle = "#fff";
      ctx.font = "bold 10px sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(text, p.x * k, p.y * k);
    }
  };
  area.corners.forEach((p, i) => p && dot(p, "#e02020", String(i + 1)));
  area.outline.forEach((p) => dot(p, "#1f6feb"));
  areaHelp();
}

function fillCornerInputs() {
  [...$("areaCorners").tBodies[0].rows].forEach((tr, i) => {
    const [x, y] = tr.querySelectorAll("input");
    x.value = area.corners[i] ? Math.round(area.corners[i].x) : "";
    y.value = area.corners[i] ? Math.round(area.corners[i].y) : "";
  });
}

function resetArea() {
  area.corners = [null, null, null, null];
  area.outline = [];
  fillCornerInputs();
  if (area.img) drawArea();
}

function buildCornerTable() {
  const body = $("areaCorners").tBodies[0];
  CORNER_NAMES.forEach((name, i) => {
    const tr = body.insertRow();
    tr.insertCell().textContent = `${i + 1}. ${name}`;
    for (const axis of ["x", "y"]) {
      const input = document.createElement("input");
      input.type = "number";
      input.min = "0";
      input.setAttribute("aria-label", `${name} corner ${axis}`);
      input.addEventListener("change", () => {
        const [x, y] = tr.querySelectorAll("input");
        area.corners[i] = x.value !== "" && y.value !== "" ? { x: Number(x.value), y: Number(y.value) } : null;
        if (area.img) drawArea();
      });
      tr.insertCell().append(input);
    }
  });
}

$("areaPhoto").addEventListener("change", () => {
  const file = $("areaPhoto").files[0];
  $("areaError").textContent = "";
  $("areaOk").textContent = "";
  if (!file) return;
  if (!["image/jpeg", "image/png"].includes(file.type)) {
    $("areaError").textContent = "Pick a JPG or PNG photo.";
    return;
  }
  if (file.size > 25 * 1024 * 1024) {
    $("areaError").textContent = "That photo is over 25 MB. Pick a smaller one.";
    return;
  }
  const img = new Image();
  const url = URL.createObjectURL(file);
  img.onload = () => {
    URL.revokeObjectURL(url);
    area.img = img; // naturalWidth/Height are the photo as it looks on screen (phone turn applied)
    area.file = file;
    const c = $("areaCanvas");
    c.width = Math.min(img.naturalWidth, 900);
    c.height = Math.round((img.naturalHeight * c.width) / img.naturalWidth);
    $("areaWork").hidden = false;
    resetArea();
  };
  img.onerror = () => {
    $("areaError").textContent = "Could not open that photo.";
  };
  img.src = url;
});

$("areaCanvas").addEventListener("click", (e) => {
  const r = e.target.getBoundingClientRect();
  const p = { x: ((e.clientX - r.left) / r.width) * area.img.naturalWidth, y: ((e.clientY - r.top) / r.height) * area.img.naturalHeight };
  const next = area.corners.findIndex((c) => !c);
  if (next >= 0) {
    area.corners[next] = p;
    fillCornerInputs();
  } else {
    area.outline.push(p);
  }
  drawArea();
});
$("areaUndo").addEventListener("click", () => {
  if (area.outline.length) area.outline.pop();
  else {
    const last = area.corners.map((c) => !!c).lastIndexOf(true);
    if (last >= 0) area.corners[last] = null;
    fillCornerInputs();
  }
  drawArea();
});
$("areaClearOutline").addEventListener("click", () => {
  area.outline = [];
  drawArea();
});
$("areaReset").addEventListener("click", resetArea);

$("areaSend").addEventListener("click", async () => {
  $("areaError").textContent = "";
  $("areaOk").textContent = "";
  if (!area.file || area.corners.some((c) => !c)) {
    $("areaError").textContent = "Pick a photo and mark all four corners first.";
    return;
  }
  if (area.outline.length && area.outline.length < 3) {
    $("areaError").textContent = "An outline needs at least three points. Add more, or clear it.";
    return;
  }
  const pts = (list) => JSON.stringify(list.map((p) => [Math.round(p.x * 10) / 10, Math.round(p.y * 10) / 10]));
  const form = new FormData();
  form.append("photo", area.file);
  form.append("quad", pts(area.corners));
  if (area.outline.length) form.append("outline", pts(area.outline));
  for (const [field, id] of [["label", "areaLabel"], ["real_width_mm", "areaW"], ["real_height_mm", "areaH"]]) {
    if ($(id).value.trim() !== "") form.append(field, $(id).value.trim());
  }
  form.append("gcode", $("areaGcode").checked ? "true" : "false");
  $("areaSend").disabled = true;
  try {
    await call("POST", `/lab/items/${itemId}/areas`, form);
    $("areaOk").textContent = "Sent. It appears below while the workshop PC reads it.";
    $("areaLabel").value = "";
    resetArea();
    await refreshScans();
  } catch (e) {
    $("areaError").textContent = e.message;
  } finally {
    $("areaSend").disabled = false;
  }
});

// After saving parts: read the saved ids back and keep them on the rows.
async function syncPartIds() {
  const def = await call("GET", `/lab/items/${itemId}/definition`);
  [...$("parts").tBodies[0].rows].forEach((tr, i) => {
    const saved = def.parts.find((p) => p.sort === i);
    if (saved) tr.dataset.partId = saved.id;
  });
  partChoices = partLabels(def.parts);
  refreshPartSelects();
}

$("addCarving").addEventListener("click", () => {
  const tr = addCarvingRow({});
  $("carvings").tBodies[0].append(tr);
  tr.querySelector("select").focus();
});
$("saveCarvings").addEventListener("click", async () => {
  $("carvingsError").textContent = "";
  $("carvingsOk").textContent = "";
  const carvings = [...$("carvings").tBodies[0].rows].map(readCarvingRow);
  if (carvings.some((c) => !c.part_id || !c.design_id)) {
    $("carvingsError").textContent = "Choose a part and a design on every row, or delete the row.";
    return;
  }
  try {
    await call("PUT", `/lab/items/${itemId}/carvings`, { carvings });
    $("carvingsOk").textContent = `Saved ${carvings.length} carvings. Open the Lab view to check them.`;
  } catch (e) {
    $("carvingsError").textContent = e.message;
  }
});

async function init() {
  buildCornerTable();
  $("viewLink").href = `/lab/view/${itemId}`;
  sizeInputs();
  try {
    const def = await call("GET", `/lab/items/${itemId}/definition`);
    $("title").textContent = `Edit parts: ${def.item.name}`;
    document.title = `Edit ${def.item.name} · 3D Lab`;
    $("isFurniture").checked = def.item.is_furniture;
    fillSizes(def);
    loadStarts(def.suggestion, !!def.dimensions_mm);
    const body = $("parts").tBodies[0];
    def.parts.forEach((p) => body.append(addRow(p)));
    (def.hardware || []).forEach((h) => $("hardware").tBodies[0].append(addHwRow(h)));
    partChoices = partLabels(def.parts);
    designChoices = (def.designs || []).map((d) => [d.id, `${d.sku} · ${d.name}`, d.has_image]);
    (def.carvings || []).forEach((c) => $("carvings").tBodies[0].append(addCarvingRow(c)));
    renderScans(def.scans || []);
  } catch (e) {
    $("partsError").textContent = e.message;
  }
}
init();
