// 3D Lab viewer (read-only). All sizes and positions come solved from the server
// (GET /lab/items/{id}); this file contains no formula code.
import * as THREE from "/lab/static/vendor/three.module.js";
import { OrbitControls } from "/lab/static/vendor/controls/OrbitControls.js";
import { GLTFLoader } from "/lab/static/vendor/loaders/GLTFLoader.js";

const itemId = location.pathname.split("/").pop();
const $ = (id) => document.getElementById(id);
const MM = 0.001; // scene units are metres

// ---------------------------------------------------------------- scene
const canvas = $("canvas");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 100);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
scene.add(new THREE.HemisphereLight(0xffffff, 0x8a6a45, 1.1));
const sun = new THREE.DirectionalLight(0xffffff, 1.4);
sun.position.set(2, 4, 3);
scene.add(sun);
const grid = new THREE.GridHelper(4, 40, 0xbf830b, 0xc9b08f);
grid.material.opacity = 0.35;
grid.material.transparent = true;
scene.add(grid);

const group = new THREE.Group();
scene.add(group);
let parts = []; // {data, mesh, centre, base}
let data = null;
let selected = null;
let framed = false;
let firstLoad = true;
renderer.setClearColor(new THREE.Color(getComputedStyle(document.body).getPropertyValue("--stage").trim() || "#f3ece0"));

function resize() {
  const r = canvas.parentElement.getBoundingClientRect();
  renderer.setSize(r.width, r.height, false);
  camera.aspect = r.width / Math.max(r.height, 1);
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(canvas.parentElement);
renderer.setAnimationLoop(() => {
  controls.update();
  renderer.render(scene, camera);
});

// one stable warm colour per kind of wood
const PALETTE = [0xb5793c, 0x8c5a2b, 0xc99a5b, 0x6f4522, 0xa8703a, 0xd2a86a];
const woodColour = (wood) => {
  let h = 0;
  for (const c of wood || "") h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
};

// ---------------------------------------------------------------- build the parts
function clearParts() {
  for (const p of parts) {
    group.remove(p.mesh);
    p.mesh.traverse((o) => {
      o.geometry?.dispose();
      o.material?.dispose?.();
    });
  }
  parts = [];
  selected = null;
}

function buildParts(solved) {
  clearParts();
  const box = new THREE.Box3();
  for (const d of solved) {
    const size = new THREE.Vector3(d.size.x, d.size.y, d.size.z).multiplyScalar(MM);
    const centre = new THREE.Vector3(d.pos.x, d.pos.y, d.pos.z).multiplyScalar(MM).add(size.clone().multiplyScalar(0.5));
    const mat = new THREE.MeshStandardMaterial({ color: woodColour(d.wood), roughness: 0.7, metalness: 0.02 });
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(size.x, size.y, size.z), mat);
    mesh.add(new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry), new THREE.LineBasicMaterial({ color: 0x3a1b04 })));
    mesh.position.copy(centre);
    mesh.userData.part = d;
    group.add(mesh);
    parts.push({ data: d, mesh, centre, size, base: mat.color.clone(), mat });
    box.expandByObject(mesh);
    if (d.has_model) loadModel(parts[parts.length - 1]);
  }
  const middle = box.getCenter(new THREE.Vector3());
  // sit the piece on the floor and centre it
  group.position.set(-middle.x, -box.min.y, -middle.z);
  grid.position.y = 0;
  parts.forEach((p) => (p.explodeDir = explodeDirection(p.centre, middle)));
  if (!framed) {
    const span = box.getSize(new THREE.Vector3()).length();
    camera.position.set(span * 0.9, span * 0.7, span * 1.1);
    controls.target.set(0, (box.max.y - box.min.y) / 2, 0);
    controls.minDistance = span * 0.3;
    controls.maxDistance = span * 6;
    framed = true;
  }
  group.userData.span = box.getSize(new THREE.Vector3()).length();
  applyExplode();
  applySteps();
}

function explodeDirection(centre, middle) {
  const d = centre.clone().sub(middle);
  if (d.lengthSq() < 1e-8) return new THREE.Vector3(0, 1, 0);
  d.y *= 1.4; // lift a little more than it spreads, like an assembly diagram
  return d.normalize();
}

// A carved part with its own model: load it, scale it to the part's box
function loadModel(p) {
  new GLTFLoader().load(
    `/lab/parts/${p.data.id}/model`,
    (gltf) => {
      const model = gltf.scene;
      const bb = new THREE.Box3().setFromObject(model);
      const s = bb.getSize(new THREE.Vector3());
      model.scale.set(p.size.x / (s.x || 1), p.size.y / (s.y || 1), p.size.z / (s.z || 1));
      const bb2 = new THREE.Box3().setFromObject(model);
      model.position.sub(bb2.getCenter(new THREE.Vector3()));
      p.mesh.material.visible = false;
      p.mesh.children.forEach((c) => (c.visible = false));
      p.mesh.add(model);
    },
    undefined,
    () => {} // keep the box if the model cannot be loaded
  );
}

// ---------------------------------------------------------------- explode and steps
function applyExplode() {
  const t = Number($("explode").value) / 100;
  const dist = (group.userData.span || 1) * 0.55 * t;
  for (const p of parts) p.mesh.position.copy(p.centre).addScaledVector(p.explodeDir, dist);
}

function applySteps() {
  const max = data?.steps || 0;
  const slider = $("step");
  slider.max = String(max);
  if (Number(slider.value) > max) slider.value = String(max);
  const n = Number(slider.value);
  const all = n >= max;
  $("stepLabel").textContent = max === 0 ? "none" : all ? "all" : `${n} of ${max}`;
  for (const p of parts) {
    p.mesh.visible = p.data.step <= n;
    const current = !all && p.data.step === n;
    p.mat.color.copy(current ? new THREE.Color(0xe8a21a) : p.base);
    p.mat.emissive.set(current ? 0x442200 : 0x000000);
  }
}

$("explode").addEventListener("input", applyExplode);
$("step").addEventListener("input", applySteps);

// ---------------------------------------------------------------- selecting a part
const ray = new THREE.Raycaster();
let downAt = null;
canvas.addEventListener("pointerdown", (e) => (downAt = [e.clientX, e.clientY]));
canvas.addEventListener("pointerup", (e) => {
  if (!downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 5) return; // it was a drag
  const r = canvas.getBoundingClientRect();
  ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
  const hit = ray.intersectObjects(parts.filter((p) => p.mesh.visible).map((p) => p.mesh), true)[0];
  let obj = hit?.object;
  while (obj && !obj.userData.part) obj = obj.parent;
  select(obj ? parts.find((p) => p.mesh === obj) : null);
});

function select(p) {
  if (selected) selected.mesh.children.find((c) => c.isLineSegments)?.material.color.set(0x3a1b04);
  selected = p;
  const box = $("partInfo");
  if (!p) {
    box.className = "note small muted";
    box.textContent = "Click a part in the 3D view.";
    return;
  }
  p.mesh.children.find((c) => c.isLineSegments)?.material.color.set(0xe02020);
  const d = p.data;
  box.className = "note small";
  box.replaceChildren();
  const line = (label, value) => {
    const row = document.createElement("div");
    const b = document.createElement("b");
    b.textContent = label + " ";
    row.append(b, document.createTextNode(value));
    box.append(row);
  };
  line("Part", d.name);
  line("Size", `${d.l} × ${d.w} × ${d.t} mm (length × width × thickness)`);
  line("Wood", d.wood || "not set");
  line("Build step", String(d.step));
  line("Position", `x ${d.pos.x}, y ${d.pos.y}, z ${d.pos.z} mm`);
  if (d.note) line("Joinery", d.note);
}

// ---------------------------------------------------------------- panel (sizes, cutting list)
function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

function renderPresets() {
  const row = $("presets");
  row.replaceChildren();
  const make = (label, key) => {
    const b = el("button", label);
    b.type = "button";
    b.setAttribute("aria-pressed", String(data.size.label === label));
    b.addEventListener("click", () => load(key ? { size: key } : {}));
    row.append(b);
  };
  make("Standard", null);
  const names = { S: "Small", M: "Medium", L: "Large" };
  for (const k of Object.keys(data.presets)) make(names[k], k);
  $("sizeNow").textContent = `${data.size.label}: ${data.size.l} × ${data.size.w} × ${data.size.h} mm`;
  $("cl").value = data.size.l;
  $("cw").value = data.size.w;
  $("ch").value = data.size.h;
}

function renderCutting() {
  const host = $("cutting");
  host.replaceChildren();
  const cl = data.cutting_list;
  if (!cl.pieces) {
    host.append(el("div", "No parts defined yet.", "muted small"));
    return;
  }
  for (const w of cl.by_wood) {
    host.append(el("div", w.wood, "wood"));
    const t = el("table");
    const head = t.createTHead().insertRow();
    for (const h of ["Part", "Qty", "L", "W", "T"]) head.append(el("th", h));
    const body = t.createTBody();
    for (const i of w.items) {
      const r = body.insertRow();
      r.append(el("td", i.name), el("td", String(i.qty)), el("td", String(i.l)), el("td", String(i.w)), el("td", String(i.t)));
    }
    const tr = body.insertRow();
    tr.className = "total";
    tr.append(el("td", `${w.pieces} pieces`), el("td", ""), el("td", ""), el("td", ""), el("td", `${w.total_m3.toFixed(4)} m³`));
    host.append(t);
  }
  host.append(el("div", `Total ${cl.total_m3.toFixed(4)} m³ in ${cl.pieces} pieces`, "small"));
}

async function load(params = {}) {
  const q = new URLSearchParams(params).toString();
  let res;
  try {
    res = await fetch(`/lab/items/${itemId}${q ? "?" + q : ""}`, { credentials: "same-origin" });
  } catch {
    $("sizeError").textContent = "Cannot reach the shop server.";
    return;
  }
  if (res.status === 401 || res.status === 403) {
    $("title").textContent = "Open the Lab from the app";
    $("subtitle").textContent = "This page needs a fresh link from the desktop or mobile app.";
    return;
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    $("sizeError").textContent = typeof body.detail === "string" ? body.detail : "Could not calculate this size.";
    return; // keep showing the last good result
  }
  $("sizeError").textContent = "";
  data = body;
  $("title").textContent = data.item.name;
  document.title = `${data.item.name} · 3D Lab`;
  renderPresets();
  renderCutting();
  if (firstLoad) {
    // start with the whole piece visible: the step slider begins at its last step
    $("step").max = String(data.steps);
    $("step").value = String(data.steps);
    firstLoad = false;
  }
  buildParts(data.parts);
  select(null);
}

$("custom").addEventListener("submit", (e) => {
  e.preventDefault();
  load({ l: $("cl").value, w: $("cw").value, h: $("ch").value });
});
$("print").addEventListener("click", () => window.print());
resize();
load();
