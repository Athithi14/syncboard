// SyncBoard client: canvas drawing + WebSocket sync + live cursors
const W = 1280, H = 720; // logical canvas size; all coordinates use this space
const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");
canvas.width = W; canvas.height = H;

const $ = (id) => document.getElementById(id);
const roomId = location.pathname.split("/").pop();
let name = localStorage.getItem("sb_name");
if (!name) {
  name = (prompt("Your display name?") || "").trim() || "Guest" + Math.floor(Math.random() * 100);
  localStorage.setItem("sb_name", name);
}

let ws, me = null, allowReconnect = true;
let users = {};          // id -> user
let strokes = [];        // all strokes (ordered)
let strokeMap = {};      // id -> stroke
let current = null;      // pen stroke being drawn by me
let pending = [];        // pen points not yet sent
let shape = null;        // {kind, color, size, a, b} while dragging a shape
const cursorEls = {};

const uid = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36);

// ---------- rendering ----------
function style(color, size) {
  ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = size;
  ctx.lineCap = "round"; ctx.lineJoin = "round";
}
function drawSegment(color, size, a, b) {
  style(color, size);
  ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
}
function drawDot(color, size, p) {
  style(color, size); ctx.beginPath(); ctx.arc(p[0], p[1], size / 2, 0, Math.PI * 2); ctx.fill();
}
function drawShape(kind, color, size, a, b) {
  style(color, size); ctx.beginPath();
  if (kind === "line") { ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); }
  else if (kind === "rect") {
    ctx.rect(Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1]));
  } else {
    ctx.ellipse((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, Math.abs(b[0] - a[0]) / 2,
      Math.abs(b[1] - a[1]) / 2, 0, 0, Math.PI * 2);
  }
  ctx.stroke();
}
function drawStroke(s) {
  if (!s.points.length) return;
  if (s.kind && s.kind !== "pen") {
    if (s.points.length >= 2) drawShape(s.kind, s.color, s.size, s.points[0], s.points[1]);
    return;
  }
  if (s.points.length === 1) return drawDot(s.color, s.size, s.points[0]);
  for (let i = 1; i < s.points.length; i++) drawSegment(s.color, s.size, s.points[i - 1], s.points[i]);
}
function redraw() {
  ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, W, H);
  strokes.forEach(drawStroke);
}
redraw();

// ---------- websocket ----------
function setStatus(t) { $("status").textContent = t; }

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/${roomId}?name=${encodeURIComponent(name)}`);
  ws.onopen = () => setStatus("Live");
  ws.onclose = () => {
    if (!allowReconnect) return;
    setStatus("Reconnecting…"); setTimeout(connect, 1500);
  };
  ws.onmessage = (e) => handle(JSON.parse(e.data));
}
function send(obj) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); }

function handle(m) {
  switch (m.type) {
    case "init":
      me = m.you;
      users = {}; m.users.forEach((u) => (users[u.id] = u));
      strokes = m.strokes; strokeMap = {}; strokes.forEach((s) => (strokeMap[s.id] = s));
      Object.keys(cursorEls).forEach(removeCursor);
      redraw(); renderUsers();
      break;
    case "error": allowReconnect = false; setStatus(m.message); break;
    case "user_join": users[m.user.id] = m.user; renderUsers(); break;
    case "user_leave": delete users[m.id]; removeCursor(m.id); renderUsers(); break;
    case "cursor": moveCursor(m.id, m.x, m.y); break;
    case "stroke_start":
      if (strokeMap[m.stroke.id]) break;
      strokes.push(m.stroke); strokeMap[m.stroke.id] = m.stroke; drawStroke(m.stroke); break;
    case "stroke_points": {
      const s = strokeMap[m.id]; if (!s) break;
      m.points.forEach((p) => {
        const prev = s.points[s.points.length - 1];
        if (prev) drawSegment(s.color, s.size, prev, p); else drawDot(s.color, s.size, p);
        s.points.push(p);
      });
      break;
    }
    case "remove_stroke":
      strokes = strokes.filter((s) => s.id !== m.id); delete strokeMap[m.id]; redraw(); break;
    case "clear": strokes = []; strokeMap = {}; redraw(); break;
  }
}

// ---------- users & cursors ----------
function renderUsers() {
  const box = $("users"); box.innerHTML = "";
  Object.values(users).forEach((u) => {
    const chip = document.createElement("span"); chip.className = "chip";
    const dot = document.createElement("i"); dot.style.background = u.color;
    chip.append(dot, document.createTextNode(u.id === (me && me.id) ? u.name + " (you)" : u.name));
    box.append(chip);
  });
}
function moveCursor(id, x, y) {
  const u = users[id]; if (!u) return;
  let el = cursorEls[id];
  if (!el) {
    el = document.createElement("div"); el.className = "cursor"; el.style.setProperty("--c", u.color);
    const label = document.createElement("span"); label.textContent = u.name; el.append(label);
    $("cursors").append(el); cursorEls[id] = el;
  }
  el.style.left = (x / W) * 100 + "%"; el.style.top = (y / H) * 100 + "%";
}
function removeCursor(id) { if (cursorEls[id]) { cursorEls[id].remove(); delete cursorEls[id]; } }

// ---------- drawing input ----------
function pos(e) {
  const r = canvas.getBoundingClientRect();
  return [Math.round(((e.clientX - r.left) * W) / r.width), Math.round(((e.clientY - r.top) * H) / r.height)];
}
let lastCursorSent = 0;

canvas.addEventListener("pointerdown", (e) => {
  canvas.setPointerCapture(e.pointerId);
  const p = pos(e);
  const tool = $("tool").value;
  const width = +$("size").value;

  if (tool === "line" || tool === "rect" || tool === "ellipse") {
    shape = { kind: tool, color: $("color").value, size: width, a: p, b: p };
    return;
  }
  const eraser = tool === "eraser";
  current = {
    id: uid(), user: me && me.id, kind: "pen",
    color: eraser ? "#ffffff" : $("color").value,
    size: eraser ? width * 3 : width,
    points: [p],
  };
  strokes.push(current); strokeMap[current.id] = current;
  drawDot(current.color, current.size, p);
  send({ type: "stroke_start", id: current.id, kind: "pen", color: current.color,
         size: current.size, points: [p] });
});

canvas.addEventListener("pointermove", (e) => {
  const p = pos(e);
  const now = performance.now();
  if (now - lastCursorSent > 30) { lastCursorSent = now; send({ type: "cursor", x: p[0], y: p[1] }); }

  if (shape) { // live preview, only sent to others once the shape is finished
    shape.b = p; redraw(); drawShape(shape.kind, shape.color, shape.size, shape.a, shape.b);
    return;
  }
  if (!current) return;
  const prev = current.points[current.points.length - 1];
  drawSegment(current.color, current.size, prev, p);
  current.points.push(p); pending.push(p);
});

function endStroke() {
  if (shape) {
    const s = shape; shape = null;
    if (Math.abs(s.b[0] - s.a[0]) + Math.abs(s.b[1] - s.a[1]) < 3) { redraw(); return; }
    const stroke = { id: uid(), user: me && me.id, kind: s.kind, color: s.color, size: s.size, points: [s.a, s.b] };
    strokes.push(stroke); strokeMap[stroke.id] = stroke; redraw();
    send({ type: "stroke_start", id: stroke.id, kind: s.kind, color: s.color, size: s.size, points: stroke.points });
    send({ type: "stroke_end", id: stroke.id });
    return;
  }
  if (!current) return;
  flush(); send({ type: "stroke_end", id: current.id }); current = null;
}
canvas.addEventListener("pointerup", endStroke);
canvas.addEventListener("pointercancel", endStroke);

// batch pen points once per animation frame instead of one message per mouse event
function flush() {
  if (current && pending.length) {
    send({ type: "stroke_points", id: current.id, points: pending.slice(0, 200) });
    pending = pending.slice(200);
    if (pending.length) flush();
  }
}
(function loop() { flush(); requestAnimationFrame(loop); })();

// ---------- toolbar ----------
$("undo").onclick = () => send({ type: "undo" });
$("redo").onclick = () => send({ type: "redo" });
$("clear").onclick = () => { if (confirm("Clear the board for everyone?")) send({ type: "clear" }); };
$("export").onclick = () => {
  canvas.toBlob((blob) => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = `syncboard-${roomId}.png`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });
};
$("copy").onclick = async () => {
  try { await navigator.clipboard.writeText(location.href); $("copy").textContent = "Link copied"; }
  catch { prompt("Copy this link:", location.href); }
  setTimeout(() => ($("copy").textContent = "Copy invite link"), 1500);
};
window.addEventListener("keydown", (e) => {
  const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
  if (mod && k === "z" && !e.shiftKey) { e.preventDefault(); send({ type: "undo" }); }
  else if (mod && (k === "y" || (k === "z" && e.shiftKey))) { e.preventDefault(); send({ type: "redo" }); }
});

connect();
