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
  ["joinery_note", "", "text"],
];

function cell(field, hint, kind, value) {
  const td = document.createElement("td");
  if (field === "name") td.className = "name";
  if (field === "joinery_note") td.className = "note";
  let input;
  if (kind === "axis") {
    input = document.createElement("select");
    for (const [v, label] of [["x", "x (left-right)"], ["y", "y (up)"], ["z", "z (front-back)"]]) {
      const o = document.createElement("option");
      o.value = v;
      o.textContent = label;
      input.append(o);
    }
    input.value = value || "x";
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
  copy.addEventListener("click", () => tr.after(rowFrom(readRow(tr))));
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
    part[el.dataset.field] = v === "" && ["wood_type", "joinery_note"].includes(el.dataset.field) ? null : v;
  }
  part.raw_material_id = tr.dataset.raw || null;
  part.model_asset_id = tr.dataset.model || null;
  return part;
}

async function call(method, path, body) {
  const res = await fetch(path, { method, headers: HEADERS, credentials: "same-origin", body: body ? JSON.stringify(body) : undefined });
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
  } catch (e) {
    $("partsError").textContent = e.message;
  }
});

async function init() {
  $("viewLink").href = `/lab/view/${itemId}`;
  sizeInputs();
  try {
    const def = await call("GET", `/lab/items/${itemId}/definition`);
    $("title").textContent = `Edit parts: ${def.item.name}`;
    document.title = `Edit ${def.item.name} · 3D Lab`;
    $("isFurniture").checked = def.item.is_furniture;
    fillSizes(def);
    const body = $("parts").tBodies[0];
    def.parts.forEach((p) => body.append(addRow(p)));
  } catch (e) {
    $("partsError").textContent = e.message;
  }
}
init();
