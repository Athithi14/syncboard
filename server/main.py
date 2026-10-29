"""SyncBoard - real-time collaborative whiteboard (FastAPI + WebSockets)."""
import json
import logging
import os
import random
import re
import string
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger("syncboard")

BASE = Path(__file__).resolve().parent
CLIENT = BASE.parent / "client"
DATA = Path(os.environ.get("SYNCBOARD_DATA_DIR", BASE / "data"))
DATA.mkdir(parents=True, exist_ok=True)

W, H = 1280, 720
MAX_USERS = int(os.environ.get("SYNCBOARD_MAX_USERS", "20"))
MAX_POINTS_PER_MSG = 200
MAX_POINTS_PER_STROKE = 20000
MAX_REDO = 50

ROOM_RE = re.compile(r"^[a-zA-Z0-9_-]{1,40}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
KINDS = {"pen", "line", "rect", "ellipse"}
COLORS = ["#e63946", "#2a9d8f", "#f4a261", "#457b9d", "#8338ec",
          "#fb5607", "#06a77d", "#d62ac0", "#3a86ff", "#ff006e"]

app = FastAPI(title="SyncBoard")


# ---------- helpers ----------
def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def clean_points(pts) -> list:
    """Keep only valid [x, y] pairs and clamp them to the canvas."""
    out = []
    if not isinstance(pts, list):
        return out
    for p in pts[:MAX_POINTS_PER_MSG]:
        if isinstance(p, (list, tuple)) and len(p) == 2 and is_num(p[0]) and is_num(p[1]):
            out.append([clamp(p[0], 0, W), clamp(p[1], 0, H)])
    return out


class RateLimiter:
    """Token bucket so one noisy client cannot flood a room."""

    def __init__(self, rate: float = 200, burst: float = 400):
        self.rate, self.burst = rate, burst
        self.tokens, self.last = burst, time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


# ---------- room state ----------
class Room:
    def __init__(self, room_id: str):
        self.id = room_id
        self.clients: dict[str, tuple[WebSocket, dict]] = {}
        self.strokes: list[dict] = []
        self.index: dict[str, dict] = {}
        self.redo: dict[str, list[dict]] = {}
        self.file = DATA / f"{room_id}.json"
        if self.file.exists():
            try:
                self.strokes = json.loads(self.file.read_text())
                self.reindex()
            except (json.JSONDecodeError, OSError):
                self.strokes = []

    def reindex(self):
        self.index = {s["id"]: s for s in self.strokes}

    def save(self):
        try:
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.strokes))
            tmp.replace(self.file)  # atomic swap, never leaves a half-written file
        except OSError:
            log.exception("could not save room %s", self.id)

    async def broadcast(self, msg: dict, exclude: str | None = None):
        dead = []
        for uid, (ws, _) in list(self.clients.items()):
            if uid == exclude:
                continue
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(uid)
        for uid in dead:
            self.clients.pop(uid, None)


rooms: dict[str, Room] = {}


def get_room(room_id: str) -> Room:
    if room_id not in rooms:
        rooms[room_id] = Room(room_id)
    return rooms[room_id]


# ---------- HTTP ----------
@app.get("/")
def home():
    code = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return RedirectResponse(f"/room/{code}")


@app.get("/room/{room_id}")
def room_page(room_id: str):
    if not ROOM_RE.match(room_id):
        return RedirectResponse("/")
    return FileResponse(CLIENT / "index.html")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "rooms": len(rooms),
        "users": sum(len(r.clients) for r in rooms.values()),
    }


# ---------- WebSocket ----------
def build_stroke(uid: str, msg: dict) -> dict:
    kind = msg.get("kind") if msg.get("kind") in KINDS else "pen"
    color = msg.get("color")
    size = msg.get("size")
    return {
        "id": str(msg.get("id") or uuid.uuid4().hex)[:32],
        "user": uid,
        "kind": kind,
        "color": color if isinstance(color, str) and COLOR_RE.match(color) else "#000000",
        "size": clamp(size, 1, 90) if is_num(size) else 4,
        "points": clean_points(msg.get("points")),
    }


@app.websocket("/ws/{room_id}")
async def ws_endpoint(ws: WebSocket, room_id: str, name: str = "Guest"):
    if not ROOM_RE.match(room_id):
        await ws.close(code=1008)
        return
    await ws.accept()

    room = get_room(room_id)
    if len(room.clients) >= MAX_USERS:
        await ws.send_json({"type": "error", "message": "This room is full."})
        await ws.close(code=1013)
        return

    uid = uuid.uuid4().hex[:8]
    user = {
        "id": uid,
        "name": (name.strip() or "Guest")[:20],
        "color": COLORS[len(room.clients) % len(COLORS)],
    }
    room.clients[uid] = (ws, user)
    limiter = RateLimiter()

    await ws.send_json({
        "type": "init",
        "you": user,
        "users": [u for _, u in room.clients.values()],
        "strokes": room.strokes,
    })
    await room.broadcast({"type": "user_join", "user": user}, exclude=uid)

    try:
        while True:
            msg = await ws.receive_json()
            if not isinstance(msg, dict) or not limiter.allow():
                continue
            t = msg.get("type")

            if t == "cursor":
                x, y = msg.get("x"), msg.get("y")
                if is_num(x) and is_num(y):
                    await room.broadcast(
                        {"type": "cursor", "id": uid, "x": clamp(x, 0, W), "y": clamp(y, 0, H)},
                        exclude=uid)

            elif t == "stroke_start":
                stroke = build_stroke(uid, msg)
                if stroke["id"] in room.index:
                    continue
                room.strokes.append(stroke)
                room.index[stroke["id"]] = stroke
                room.redo.pop(uid, None)  # a new action invalidates redo history
                await room.broadcast({"type": "stroke_start", "stroke": stroke}, exclude=uid)

            elif t == "stroke_points":
                stroke = room.index.get(msg.get("id"))
                if stroke and stroke["user"] == uid and stroke["kind"] == "pen":
                    pts = clean_points(msg.get("points"))
                    if len(stroke["points"]) + len(pts) <= MAX_POINTS_PER_STROKE:
                        stroke["points"].extend(pts)
                        await room.broadcast(
                            {"type": "stroke_points", "id": stroke["id"], "points": pts},
                            exclude=uid)

            elif t == "stroke_end":
                room.save()

            elif t == "undo":
                mine = [s for s in room.strokes if s["user"] == uid]
                if mine:
                    last = mine[-1]
                    room.strokes.remove(last)
                    room.reindex()
                    stack = room.redo.setdefault(uid, [])
                    stack.append(last)
                    del stack[:-MAX_REDO]
                    room.save()
                    await room.broadcast({"type": "remove_stroke", "id": last["id"]})

            elif t == "redo":
                stack = room.redo.get(uid)
                if stack:
                    stroke = stack.pop()
                    room.strokes.append(stroke)
                    room.index[stroke["id"]] = stroke
                    room.save()
                    await room.broadcast({"type": "stroke_start", "stroke": stroke})

            elif t == "clear":
                room.strokes.clear()
                room.index.clear()
                room.redo.clear()
                room.save()
                await room.broadcast({"type": "clear"})

    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("websocket error in room %s", room_id)
    finally:
        room.clients.pop(uid, None)
        room.redo.pop(uid, None)
        await room.broadcast({"type": "user_leave", "id": uid})
        if not room.clients:
            rooms.pop(room_id, None)


app.mount("/static", StaticFiles(directory=CLIENT), name="static")
