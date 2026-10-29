# SyncBoard – Real-Time Collaborative Whiteboard

![CI](https://github.com/YOUR_USERNAME/syncboard/actions/workflows/ci.yml/badge.svg)

Draw together on a shared canvas with live cursors, shapes, undo/redo and private rooms.
Built with **FastAPI WebSockets** and the **HTML5 Canvas API**, tested and deployed through
a **GitHub Actions CI/CD pipeline**.

**Live demo:** _add your Render URL here_

## Features
- Multi-user drawing in real time (pen, line, rectangle, ellipse, eraser)
- Live cursors with names and colors
- Private rooms via URL (`/room/abc123`)
- Per-user undo / redo (Ctrl+Z, Ctrl+Y), clear board, export to PNG
- Boards saved to disk (atomic writes) and restored when users return
- Auto-reconnect, room capacity limit, input validation, per-client rate limiting
- `/health` endpoint for monitoring

## Architecture
```
 Browser A ──┐                          ┌── Browser B
 (Canvas)    │    JSON over WebSocket   │   (Canvas)
             ├──► FastAPI  /ws/{room} ◄─┤
 Browser C ──┘    in-memory room state  └── ...
                  + JSON file per room
```

## CI/CD pipeline
```
 git push ──► CI (ci.yml)                          CD (cd.yml, main only)
              ├─ ruff lint + pytest (3.11, 3.12)   ├─ build image ► ghcr.io
              ├─ node --check client/app.js        └─ trigger Render deploy hook
              └─ docker build + /health smoke test
```
Pull requests run CI only. Deployment happens only after CI passes on `main`.

## Run locally
```bash
cd server
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
uvicorn main:app --reload        # http://localhost:8000
pytest -v                        # run the tests
ruff check ..                    # lint
```

Or with Docker:
```bash
docker build -t syncboard . && docker run -p 8000:8000 syncboard
```

## Configuration
| Variable | Default | Purpose |
|---|---|---|
| `PORT` | 8000 | Port used in Docker |
| `SYNCBOARD_MAX_USERS` | 20 | Max people per room |
| `SYNCBOARD_DATA_DIR` | `server/data` | Where boards are saved |

## Challenges solved
- **Concurrent drawing:** each stroke has a unique ID and an owner, so simultaneous strokes never overwrite each other; the server decides the order.
- **Network load:** cursor events are throttled (~30 ms) and pen points are batched once per animation frame; a token-bucket limiter protects the server from floods.
- **Late joiners and reconnects:** the server sends the full stroke history on connect, so every client converges to the same board.
- **Untrusted input:** colors, sizes, shapes and coordinates are validated and clamped server-side.
