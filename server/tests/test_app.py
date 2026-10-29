import pytest
from starlette.websockets import WebSocketDisconnect

import main


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_root_redirects_to_room(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"].startswith("/room/")


def test_room_page_is_served(client):
    r = client.get("/room/abc123")
    assert r.status_code == 200
    assert "SyncBoard" in r.text


def test_invalid_room_id_redirects(client):
    r = client.get("/room/bad!id", follow_redirects=False)
    assert r.status_code in (302, 307)


def test_websocket_rejects_invalid_room(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/bad!id"):
            pass


def test_init_message(client):
    with client.websocket_connect("/ws/t-init?name=Ann") as ws:
        m = ws.receive_json()
        assert m["type"] == "init"
        assert m["you"]["name"] == "Ann"
        assert m["strokes"] == []


def test_stroke_broadcast_and_late_joiner_gets_history(client):
    with client.websocket_connect("/ws/t-bc?name=A") as a:
        a.receive_json()
        with client.websocket_connect("/ws/t-bc?name=B") as b:
            b.receive_json()
            assert a.receive_json()["type"] == "user_join"

            a.send_json({"type": "stroke_start", "id": "s1", "kind": "pen",
                         "color": "#112233", "size": 4, "points": [[10, 10]]})
            m = b.receive_json()
            assert m["type"] == "stroke_start" and m["stroke"]["id"] == "s1"

            a.send_json({"type": "stroke_points", "id": "s1", "points": [[20, 20]]})
            assert b.receive_json()["points"] == [[20, 20]]
            a.send_json({"type": "stroke_end", "id": "s1"})

        with client.websocket_connect("/ws/t-bc?name=C") as c:
            init = c.receive_json()
            assert len(init["strokes"]) == 1
            assert len(init["strokes"][0]["points"]) == 2


def test_cursor_is_relayed(client):
    with client.websocket_connect("/ws/t-cur?name=A") as a:
        a.receive_json()
        with client.websocket_connect("/ws/t-cur?name=B") as b:
            b.receive_json()
            a.receive_json()
            a.send_json({"type": "cursor", "x": 100, "y": 50})
            m = b.receive_json()
            assert m["type"] == "cursor" and m["x"] == 100 and m["y"] == 50


def test_bad_values_are_sanitised(client):
    with client.websocket_connect("/ws/t-san?name=A") as a:
        a.receive_json()
        with client.websocket_connect("/ws/t-san?name=B") as b:
            b.receive_json()
            a.receive_json()
            a.send_json({"type": "stroke_start", "id": "x", "kind": "evil", "color": "red",
                         "size": 9999, "points": [[-5, 99999], ["a", 1]]})
            s = b.receive_json()["stroke"]
            assert s["kind"] == "pen"
            assert s["color"] == "#000000"
            assert s["size"] == 90
            assert s["points"] == [[0, 720]]


def test_undo_then_redo(client):
    with client.websocket_connect("/ws/t-undo?name=A") as a:
        a.receive_json()
        a.send_json({"type": "stroke_start", "id": "u1", "kind": "line",
                     "color": "#000000", "size": 3, "points": [[0, 0], [50, 50]]})
        a.send_json({"type": "undo"})
        assert a.receive_json() == {"type": "remove_stroke", "id": "u1"}
        a.send_json({"type": "redo"})
        m = a.receive_json()
        assert m["type"] == "stroke_start" and m["stroke"]["id"] == "u1"


def test_clear_empties_board(client):
    with client.websocket_connect("/ws/t-clr?name=A") as a:
        a.receive_json()
        a.send_json({"type": "stroke_start", "id": "c1", "points": [[1, 1]]})
        a.send_json({"type": "clear"})
        assert a.receive_json() == {"type": "clear"}


def test_full_room_is_refused(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_USERS", 1)
    with client.websocket_connect("/ws/t-full?name=A") as a:
        a.receive_json()
        with client.websocket_connect("/ws/t-full?name=B") as b:
            assert b.receive_json()["type"] == "error"


def test_rate_limiter_blocks_floods():
    limiter = main.RateLimiter(rate=1, burst=3)
    assert [limiter.allow() for _ in range(5)].count(True) == 3
