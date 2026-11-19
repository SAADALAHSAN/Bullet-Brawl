import http.server
import json
import os
import time
import random
import socketserver
import hashlib
import base64
import struct
import urllib.parse
import threading

PORT = int(os.environ.get("PORT", 3000))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_DIR = os.path.join(BASE_DIR, "client")

active_duels = {}
active_game_rooms = {}
rooms_lock = threading.Lock()
duels_lock = threading.Lock()

user_profile = {
    "id": "player_local",
    "userId": "player_local",
    "name": "Player",
    "handle": "Player",
    "x": {"id": "player_local", "username": "Player"},
    "avatarUrl": None,
    "characterChosen": True,
    "access": {"open": True},
    "allowlist": None,
    "points": 2500,
    "kills": 12,
}

user_character = {
    "spec": {
        "species": "bear",
        "colors": {
            "body": 16740270,
            "belly": 16774368,
            "secondary": 4053940,
            "accent": 6273279,
        },
        "pattern": "spots",
        "eyes": "determined",
        "mouth": "smile",
        "headwear": "headband",
        "eyewear": "none",
        "outfit": "hoodie",
        "back": "none",
        "shoes": "sneakers",
        "jerseyNumber": 7,
        "hair": "none",
        "tint": {
            "hair": 1841698,
            "top": 16740270,
            "pants": 2964060,
        },
    },
    "source": "custom",
}

# --- Ephemeral In-Memory Friends & Messaging Store ---
# Zero user data is stored on disk on the server.
# Stored strictly in RAM, capped to keep server ultra-lightweight and private.
friends_lock = threading.Lock()
in_memory_friends_db = {
    "friends": [],
    "incoming": [],
    "outgoing": [],
    "messages": {}
}

def load_friends_db():
    return in_memory_friends_db

def save_friends_db(data):
    # Keep memory lightweight & bounded:
    # Cap messages to last 20 messages per conversation
    for fid in list(data.get("messages", {}).keys()):
        if len(data["messages"][fid]) > 20:
            data["messages"][fid] = data["messages"][fid][-20:]
    # Cap friends & requests to max 50
    if len(data.get("friends", [])) > 50:
        data["friends"] = data["friends"][:50]
    if len(data.get("incoming", [])) > 50:
        data["incoming"] = data["incoming"][:50]
    if len(data.get("outgoing", [])) > 50:
        data["outgoing"] = data["outgoing"][:50]


MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".wasm": "application/wasm",
}

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

def make_ws_accept(key):
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode("utf-8")).digest()).decode("utf-8")

def encode_ws_frame(data, opcode=1):
    b0 = 0x80 | (opcode & 0x0F)
    length = len(data)
    if length < 126:
        header = bytes([b0, length])
    elif length < 65536:
        header = bytes([b0, 126]) + struct.pack(">H", length)
    else:
        header = bytes([b0, 127]) + struct.pack(">Q", length)
    return header + data

def read_exact(rfile, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = rfile.read(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)

def decode_ws_frame(rfile):
    b0 = read_exact(rfile, 1)
    if not b0:
        return None, None
    b1 = read_exact(rfile, 1)
    if not b1:
        return None, None
    opcode = b0[0] & 0x0F
    is_masked = bool(b1[0] & 0x80)
    length = b1[0] & 0x7F
    if length == 126:
        ext = read_exact(rfile, 2)
        if not ext: return None, None
        length = struct.unpack(">H", ext)[0]
    elif length == 127:
        ext = read_exact(rfile, 8)
        if not ext: return None, None
        length = struct.unpack(">Q", ext)[0]

    mask = read_exact(rfile, 4) if is_masked else None
    if is_masked and not mask:
        return None, None

    MAX_WS_FRAME = 2 * 1024 * 1024 # 2MB limit
    if length > MAX_WS_FRAME or length < 0:
        return None, None
    payload = read_exact(rfile, length) if length > 0 else b""
    if payload is None:
        return None, None

    if is_masked:
        payload = bytes([b ^ mask[i % 4] for i, b in enumerate(payload)])
    return opcode, payload


class ArenaRequestHandler(http.server.BaseHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        self.ws_send_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        pass

    def send_ws_bytes(self, payload, opcode=2):
        frame = encode_ws_frame(payload, opcode=opcode)
        with self.ws_send_lock:
            try:
                self.wfile.write(frame)
                self.wfile.flush()
                return True
            except:
                return False

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Credentials", "true")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors_headers()
        self.end_headers()

    def send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_websocket(self, pathname, query):
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error(400, "Missing Sec-WebSocket-Key")
            return

        accept_key = make_ws_accept(key)
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept_key)
        self.end_headers()
        self.wfile.flush()

        if pathname == "/ping":
            try:
                while True:
                    opcode, payload = decode_ws_frame(self.rfile)
                    if opcode is None or opcode == 8:
                        try:
                            self.wfile.write(encode_ws_frame(b"", opcode=8))
                            self.wfile.flush()
                        except:
                            pass
                        break
                    elif opcode == 9:
                        self.send_ws_bytes(payload, opcode=10)
                    elif opcode in (1, 2):
                        self.send_ws_bytes(payload, opcode=opcode)
            except (ConnectionResetError, BrokenPipeError):
                pass
        elif pathname == "/ws":
            room_id = query.get("room", ["arena_room_1"])[0]
            role = query.get("role", ["host"])[0]
            client_id = query.get("id", [f"c_{random.randint(10000, 99999)}"])[0]

            if role == "host":
                with rooms_lock:
                    if room_id not in active_game_rooms:
                        active_game_rooms[room_id] = {
                            "host": self,
                            "clients": {},
                            "mode": query.get("mode", ["ffa"])[0],
                            "created_at": time.time()
                        }
                    else:
                        active_game_rooms[room_id]["host"] = self
                    room = active_game_rooms[room_id]

                print(f"[WS Room] Host connected to room {room_id}")

                try:
                    while True:
                        opcode, payload = decode_ws_frame(self.rfile)
                        if opcode is None or opcode == 8:
                            break
                        if opcode in (1, 2) and len(payload) >= 2:
                            cmd = payload[0]
                            id_len = payload[1]
                            if len(payload) >= 2 + id_len:
                                target_cid = payload[2:2+id_len].decode("utf-8", errors="ignore")
                                packet = payload[2+id_len:]
                                with rooms_lock:
                                    clients = list(room["clients"].items())
                                if target_cid == "*":
                                    for cid, client_handler in clients:
                                        client_handler.send_ws_bytes(packet, opcode=opcode)
                                else:
                                    with rooms_lock:
                                        client_handler = room["clients"].get(target_cid)
                                    if client_handler:
                                        client_handler.send_ws_bytes(packet, opcode=opcode)
                except (ConnectionResetError, BrokenPipeError):
                    pass
                finally:
                    print(f"[WS Room] Host disconnected from room {room_id}")
                    with rooms_lock:
                        clients_to_close = []
                        if room_id in active_game_rooms and active_game_rooms[room_id].get("host") == self:
                            active_game_rooms[room_id]["host"] = None
                            clients_to_close = list(room["clients"].values())
                            active_game_rooms.pop(room_id, None)
                    for ch in clients_to_close:
                        try:
                            ch.wfile.write(encode_ws_frame(b"", opcode=8))
                            ch.wfile.flush()
                        except:
                            pass

            elif role == "client":
                with rooms_lock:
                    room = active_game_rooms.get(room_id)
                    if room and room.get("host"):
                        room["clients"][client_id] = self
                        host_handler = room["host"]
                    else:
                        host_handler = None

                if not host_handler:
                    print(f"[WS Room] Client {client_id} rejected: no host in room {room_id}")
                    try:
                        self.wfile.write(encode_ws_frame(b"", opcode=8))
                        self.wfile.flush()
                    except:
                        pass
                    return

                print(f"[WS Room] Client {client_id} connected to room {room_id}")
                cid_bytes = client_id.encode("utf-8")
                join_notify = bytes([1, len(cid_bytes)]) + cid_bytes
                host_handler.send_ws_bytes(join_notify, opcode=2)

                try:
                    while True:
                        opcode, payload = decode_ws_frame(self.rfile)
                        if opcode is None or opcode == 8:
                            break
                        if opcode in (1, 2):
                            fwd = bytes([2, len(cid_bytes)]) + cid_bytes + payload
                            with rooms_lock:
                                current_host = room.get("host")
                            if current_host:
                                current_host.send_ws_bytes(fwd, opcode=opcode)
                except (ConnectionResetError, BrokenPipeError):
                    pass
                finally:
                    print(f"[WS Room] Client {client_id} disconnected from room {room_id}")
                    with rooms_lock:
                        if room_id in active_game_rooms:
                            active_game_rooms[room_id]["clients"].pop(client_id, None)
                        current_host = room.get("host") if room else None
                    if current_host:
                        leave_notify = bytes([3, len(cid_bytes)]) + cid_bytes
                        current_host.send_ws_bytes(leave_notify, opcode=2)

    def do_GET(self):
        try:
            parsed_url = urllib.parse.urlparse(self.path)
            pathname = parsed_url.path
            query = urllib.parse.parse_qs(parsed_url.query)

            if self.headers.get("Upgrade", "").lower() == "websocket":
                self.handle_websocket(pathname, query)
                return

            host = self.headers.get("Host", f"localhost:{PORT}")

            if pathname == "/api/health":
                self.send_json({
                    "ok": True,
                    "x": False,
                    "vision": False,
                    "turnstile": None,
                    "guests": True,
                    "gate": False,
                })
                return

            if pathname == "/api/me":
                self.send_json(user_profile)
                return

            is_ssl = (self.headers.get("X-Forwarded-Proto") == "https" or 
                      self.headers.get("X-Forwarded-Ssl") == "on" or
                      self.headers.get("X-Forwarded-Port") == "443")
            ws_protocol = "wss" if is_ssl else "ws"

            if pathname == "/api/ping":
                self.send_json({"ok": True, "pong": int(time.time() * 1000)})
                return

            if pathname == "/api/regions":
                self.send_json([
                    {
                        "id": "global",
                        "name": "Official Server (Global)",
                        "pingUrl": "/api/ping",
                        "online": True,
                    }
                ])
                return

            if pathname == "/api/character":
                self.send_json(user_character)
                return

            # --- Friends GET Endpoints ---
            if pathname == "/api/friends":
                with friends_lock:
                    db = load_friends_db()
                self.send_json({
                    "friends": db["friends"],
                    "incoming": db["incoming"],
                    "invites": db["incoming"],
                    "outgoing": db["outgoing"],
                    "requests": len(db["incoming"]),
                })
                return

            if pathname.startswith("/api/friends/") and pathname.endswith("/messages"):
                raw_id = pathname[len("/api/friends/"): -len("/messages")]
                friend_id = urllib.parse.unquote(raw_id).strip()
                with friends_lock:
                    db = load_friends_db()
                    msgs = db["messages"].get(friend_id, [])
                    if not msgs:
                        for fid, m_list in db["messages"].items():
                            if fid.lower() == friend_id.lower() or fid.lower() == f"usr_{friend_id.lower()}":
                                msgs = m_list
                                break
                self.send_json({"messages": msgs})
                return

            if pathname == "/api/messages":
                raw_id = query.get("with", [""])[0]
                friend_id = urllib.parse.unquote(raw_id).strip()
                with friends_lock:
                    db = load_friends_db()
                    msgs = db["messages"].get(friend_id, [])
                self.send_json({"messages": msgs})
                return

            if pathname == "/api/party":
                with friends_lock:
                    db = load_friends_db()
                self.send_json({
                    "party": None,
                    "invites": [],
                    "requests": len(db.get("incoming", []))
                })
                return

            if pathname == "/api/season":
                self.send_json({
                    "id": "season-1",
                    "name": "Bullet Brawl Season 1",
                    "endsAt": None,
                    "pointsPerKill": 100,
                    "pointsPerAssist": 50,
                    "tokenSymbol": "COINS",
                })
                return

            if pathname == "/api/leaderboard":
                self.send_json([
                    {"rank": 1, "name": user_profile["name"], "kills": user_profile["kills"], "points": user_profile["points"]},
                    {"rank": 2, "name": "NeonShadow", "kills": 9, "points": 2100},
                    {"rank": 3, "name": "PixelReaper", "kills": 7, "points": 1800},
                    {"rank": 4, "name": "CrateCrusher", "kills": 4, "points": 1200},
                ])
                return

            if pathname == "/api/access" or pathname.startswith("/api/access/"):
                self.send_json({"open": True, "ok": True})
                return

            if pathname.startswith("/api/wallet"):
                self.send_json({"enabled": False, "linked": False})
                return

            if pathname.startswith("/api/duel/"):
                duel_code = urllib.parse.unquote(pathname[len("/api/duel/"):].upper())
                with duels_lock:
                    duel_data = active_duels.get(duel_code, {
                        "code": duel_code,
                        "host": "Challenger",
                        "hostAvatar": None,
                        "opponent": user_profile["name"],
                        "target": 5,
                        "region": "local",
                        "canJoin": True,
                        "youAreHost": False,
                        "expired": False,
                    })
                self.send_json(duel_data)
                return

            # Serve static files from CLIENT_DIR
            rel_path = pathname.lstrip("/")
            if not rel_path:
                rel_path = "index.html"

            file_path = os.path.abspath(os.path.join(CLIENT_DIR, rel_path))
            client_abs = os.path.abspath(CLIENT_DIR)
            try:
                if os.path.commonpath([client_abs, file_path]) != client_abs:
                    self.send_error(403, "Forbidden")
                    return
            except Exception:
                self.send_error(403, "Forbidden")
                return

            if not os.path.isfile(file_path):
                _, ext = os.path.splitext(pathname)
                if not ext:
                    file_path = os.path.join(CLIENT_DIR, "index.html")
                else:
                    self.send_error(404, f"Not Found: {pathname}")
                    return

            ext = os.path.splitext(file_path)[1].lower()
            content_type = MIME_TYPES.get(ext, "application/octet-stream")

            with open(file_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            if ext in [".js", ".html", ".css", ".json"]:
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(content)
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            try:
                self.send_error(500, f"Internal Error: {e}")
            except:
                pass

    def do_POST(self):
        try:
            parsed_url = urllib.parse.urlparse(self.path)
            pathname = parsed_url.path
            host = self.headers.get("Host", f"localhost:{PORT}")

            try:
                raw_cl = int(self.headers.get("Content-Length", 0))
                content_length = max(0, min(raw_cl, 5 * 1024 * 1024))
            except (ValueError, TypeError):
                content_length = 0
            body = self.rfile.read(content_length).decode("utf-8", errors="ignore") if content_length > 0 else ""
            try:
                parsed = json.loads(body) if body else {}
            except:
                parsed = {}

            # --- Friends & Chat POST Endpoints ---
            if pathname == "/api/friends":
                handle = parsed.get("handle", "").strip().lstrip("@")
                if not handle:
                    self.send_json({"error": "Username required"}, status=400)
                    return

                target_id = f"usr_{handle.lower().replace(' ', '_')}"

                with friends_lock:
                    db = load_friends_db()

                    # 1. If currently in incoming -> auto accept
                    for i, inc in enumerate(db["incoming"]):
                        inc_uid = str(inc.get("userId", "")).lower()
                        inc_handle = str(inc.get("handle", "")).lower()
                        if inc_handle == handle.lower() or inc_uid == target_id:
                            accepted = db["incoming"].pop(i)
                            accepted["online"] = True
                            accepted["state"] = "menu"
                            if not any(f.get("userId") == accepted.get("userId") for f in db["friends"]):
                                db["friends"].append(accepted)
                            db["outgoing"] = [o for o in db["outgoing"] if o.get("userId") != accepted.get("userId") and o.get("handle", "").lower() != handle.lower()]
                            save_friends_db(db)
                            self.send_json({"status": "accepted", "handle": accepted["handle"], "userId": accepted["userId"]})
                            return

                    # 2. If already in friends
                    for fr in db["friends"]:
                        fr_uid = str(fr.get("userId", "")).lower()
                        fr_handle = str(fr.get("handle", "")).lower()
                        if fr_handle == handle.lower() or fr_uid == target_id:
                            self.send_json({"status": "already", "handle": fr["handle"], "userId": fr["userId"]})
                            return

                    # 3. If already in outgoing
                    for out in db["outgoing"]:
                        out_uid = str(out.get("userId", "")).lower()
                        out_handle = str(out.get("handle", "")).lower()
                        if out_handle == handle.lower() or out_uid == target_id:
                            self.send_json({"status": "pending", "handle": out["handle"], "userId": out["userId"]})
                            return

                    # 4. Add to outgoing request (No bot auto-accept)
                    new_req = {
                        "userId": target_id,
                        "name": handle,
                        "handle": handle,
                        "avatarUrl": None,
                        "online": False,
                        "state": "offline"
                    }
                    db["outgoing"].append(new_req)
                    save_friends_db(db)

                self.send_json({"status": "sent", "handle": handle, "userId": target_id})
                return

            # Receive an incoming friend request (from another player or external)
            if pathname == "/api/friends/incoming" or pathname == "/api/friends/request":
                from_user = parsed.get("handle") or parsed.get("name") or parsed.get("from") or ""
                from_user = from_user.strip().lstrip("@")
                if not from_user:
                    self.send_json({"error": "Sender username required"}, status=400)
                    return
                from_id = parsed.get("userId") or f"usr_{from_user.lower().replace(' ', '_')}"
                with friends_lock:
                    db = load_friends_db()
                    if any(f.get("userId") == from_id or f.get("handle", "").lower() == from_user.lower() for f in db["friends"]):
                        self.send_json({"status": "already"})
                        return
                    if not any(i.get("userId") == from_id or i.get("handle", "").lower() == from_user.lower() for i in db["incoming"]):
                        db["incoming"].append({
                            "userId": from_id,
                            "name": from_user,
                            "handle": from_user,
                            "avatarUrl": parsed.get("avatarUrl"),
                            "online": True,
                            "state": "menu"
                        })
                        save_friends_db(db)
                self.send_json({"ok": True, "status": "received"})
                return

            # Accept friend request: POST /api/friends/<id>/accept
            if "/accept" in pathname and pathname.startswith("/api/friends/"):
                raw_id = pathname[len("/api/friends/"):].split("/accept")[0].strip("/")
                friend_id = urllib.parse.unquote(raw_id).strip()

                with friends_lock:
                    db = load_friends_db()
                    found = None
                    fid = friend_id.lower()
                    for i, inc in enumerate(db["incoming"]):
                        inc_uid = str(inc.get("userId", "")).lower()
                        inc_handle = str(inc.get("handle", "")).lower()
                        inc_name = str(inc.get("name", "")).lower()
                        if (inc_uid == fid or 
                            inc_handle == fid or 
                            inc_name == fid or 
                            inc_uid == f"usr_{fid}" or 
                            f"usr_{inc_handle}" == fid or
                            inc_uid.replace("usr_", "") == fid.replace("usr_", "")):
                            found = db["incoming"].pop(i)
                            break

                    if found:
                        found["online"] = True
                        found["state"] = "menu"
                        if not any(f.get("userId") == found.get("userId") for f in db["friends"]):
                            db["friends"].append(found)

                        db["outgoing"] = [
                            o for o in db["outgoing"] 
                            if o.get("userId") != found.get("userId") and 
                               o.get("handle", "").lower() != found.get("handle", "").lower()
                        ]

                        if found.get("userId") not in db["messages"]:
                            db["messages"][found.get("userId")] = []

                        save_friends_db(db)
                        self.send_json({"ok": True, "friend": found})
                        return

                    # If not in incoming, check if already in friends
                    for fr in db["friends"]:
                        fr_uid = str(fr.get("userId", "")).lower()
                        fr_handle = str(fr.get("handle", "")).lower()
                        if fr_uid == fid or fr_handle == fid:
                            self.send_json({"ok": True, "already": True, "friend": fr})
                            return

                self.send_json({"error": f"Friend request not found for '{friend_id}'"}, status=404)
                return

            if pathname.startswith("/api/friends/") and pathname.endswith("/messages"):
                raw_id = pathname[len("/api/friends/"): -len("/messages")]
                friend_id = urllib.parse.unquote(raw_id).strip()
                text = parsed.get("text", "").strip()
                if not text:
                    self.send_json({"error": "Empty message"}, status=400)
                    return

                msg = {
                    "id": f"msg_{int(time.time()*1000)}_{random.randint(100, 999)}",
                    "sender": user_profile["name"],
                    "senderId": user_profile["userId"],
                    "text": text,
                    "timestamp": int(time.time() * 1000)
                }

                with friends_lock:
                    db = load_friends_db()
                    if friend_id not in db["messages"]:
                        db["messages"][friend_id] = []
                    db["messages"][friend_id].append(msg)
                    save_friends_db(db)

                self.send_json({"ok": True, "message": msg})
                return

            if pathname == "/api/messages":
                friend_id = parsed.get("to", "").strip()
                text = parsed.get("text", "").strip()
                if not friend_id or not text:
                    self.send_json({"error": "Recipient and text required"}, status=400)
                    return

                msg = {
                    "id": f"msg_{int(time.time()*1000)}_{random.randint(100, 999)}",
                    "sender": user_profile["name"],
                    "senderId": user_profile["userId"],
                    "text": text,
                    "timestamp": int(time.time() * 1000)
                }

                with friends_lock:
                    db = load_friends_db()
                    if friend_id not in db["messages"]:
                        db["messages"][friend_id] = []
                    db["messages"][friend_id].append(msg)
                    save_friends_db(db)

                self.send_json({"ok": True, "message": msg})
                return

            if pathname == "/api/matchmake":
                mode = parsed.get("mode", "ffa")
                duel = parsed.get("duel")
                host_header = self.headers.get("Host", f"localhost:{PORT}")

                with rooms_lock:
                    now = time.time()
                    stale_rooms = [rid for rid, rdata in active_game_rooms.items()
                                   if rdata.get("host") is None and now - rdata.get("created_at", 0) > 900]
                    for rid in stale_rooms:
                        active_game_rooms.pop(rid, None)

                    if duel:
                        room_id = f"duel_{duel}"
                        is_host = (room_id not in active_game_rooms) or (active_game_rooms[room_id].get("host") is None)
                    else:
                        room_id = None
                        for rid, rdata in active_game_rooms.items():
                            if rdata["mode"] == mode and rdata.get("host") is not None and len(rdata.get("clients", {})) < 12:
                                room_id = rid
                                break
                        if not room_id:
                            room_id = f"{mode}_{int(time.time()*1000)}"
                            is_host = True
                        else:
                            is_host = False

                is_ssl = (self.headers.get("X-Forwarded-Proto") == "https" or 
                          self.headers.get("X-Forwarded-Ssl") == "on" or
                          self.headers.get("X-Forwarded-Port") == "443")
                ws_scheme = "wss" if is_ssl else "ws"
                client_id = f"c_{random.randint(10000, 99999)}"
                role = "host" if is_host else "client"
                ws_url = f"{ws_scheme}://{host_header}/ws?room={room_id}&role={role}&id={client_id}"

                self.send_json({
                    "wsUrl": ws_url,
                    "joinToken": f"player_token_{int(time.time() * 1000)}",
                    "mode": mode,
                    "roomId": room_id,
                    "isHost": is_host,
                })
                return

            if pathname == "/api/duel":
                code = f"DUEL{random.randint(1000, 9999)}"
                opp = parsed.get("opponent", "").strip().lstrip("@")
                duel_data = {
                    "code": code,
                    "host": user_profile["name"],
                    "hostAvatar": user_profile["avatarUrl"],
                    "opponent": opp,
                    "target": 5,
                    "region": parsed.get("region", "local"),
                    "canJoin": True,
                    "youAreHost": True,
                    "expired": False,
                    "created_at": time.time(),
                }
                with duels_lock:
                    now = time.time()
                    expired_duels = [k for k, v in active_duels.items() if now - v.get("created_at", 0) > 3600]
                    for k in expired_duels:
                        active_duels.pop(k, None)
                    active_duels[code] = duel_data
                if opp:
                    with friends_lock:
                        db = load_friends_db()
                        for fr in db["friends"]:
                            if fr.get("handle", "").lower() == opp.lower() or fr.get("name", "").lower() == opp.lower():
                                fid = fr.get("userId")
                                if fid not in db["messages"]:
                                    db["messages"][fid] = []
                                db["messages"][fid].append({
                                    "id": f"msg_{int(time.time()*1000)}",
                                    "sender": user_profile["name"],
                                    "senderId": user_profile["userId"],
                                    "text": f"⚔️ I challenged you to a 1v1 duel! Room code: {code}",
                                    "timestamp": int(time.time() * 1000)
                                })
                                save_friends_db(db)
                                break
                self.send_json(duel_data)
                return

            if pathname == "/api/auth/logout":
                self.send_json({"ok": True})
                return

            self.send_error(404, "Not Found")
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            try:
                self.send_error(500, f"Internal Error: {e}")
            except:
                pass

    def do_DELETE(self):
        try:
            parsed_url = urllib.parse.urlparse(self.path)
            pathname = parsed_url.path

            if pathname.startswith("/api/friends/"):
                raw_id = pathname[len("/api/friends/"):].rstrip("/")
                friend_id = urllib.parse.unquote(raw_id).strip().lower()

                with friends_lock:
                    db = load_friends_db()
                    
                    def matches(item):
                        uid = str(item.get("userId", "")).lower()
                        handle = str(item.get("handle", "")).lower()
                        name = str(item.get("name", "")).lower()
                        return (uid == friend_id or 
                                handle == friend_id or 
                                name == friend_id or 
                                uid.replace("usr_", "") == friend_id.replace("usr_", ""))

                    db["friends"] = [f for f in db["friends"] if not matches(f)]
                    db["incoming"] = [f for f in db["incoming"] if not matches(f)]
                    db["outgoing"] = [f for f in db["outgoing"] if not matches(f)]
                    save_friends_db(db)

                self.send_json({"ok": True})
                return

            self.send_error(404, "Not Found")
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            try:
                self.send_error(500, f"Internal Error: {e}")
            except:
                pass

    def do_PUT(self):
        try:
            parsed_url = urllib.parse.urlparse(self.path)
            pathname = parsed_url.path

            try:
                raw_cl = int(self.headers.get("Content-Length", 0))
                content_length = max(0, min(raw_cl, 5 * 1024 * 1024))
            except (ValueError, TypeError):
                content_length = 0
            body = self.rfile.read(content_length).decode("utf-8", errors="ignore") if content_length > 0 else ""
            try:
                parsed = json.loads(body) if body else {}
            except:
                self.send_json({"error": "Invalid JSON"}, status=400)
                return

            if pathname == "/api/character":
                if "name" in parsed and parsed["name"]:
                    name = str(parsed["name"])[:15]
                    user_profile["name"] = name
                    user_profile["handle"] = name
                    if user_profile.get("x"):
                        user_profile["x"]["username"] = name
                if "spec" in parsed:
                    user_character["spec"] = parsed["spec"]
                user_character.update({k: v for k, v in parsed.items() if k != "spec"})
                self.send_json({"ok": True, "character": user_character, "profile": user_profile})
                return

            self.send_error(404, "Not Found")
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            try:
                self.send_error(500, f"Internal Error: {e}")
            except:
                pass


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    server = ThreadedHTTPServer(("0.0.0.0", PORT), ArenaRequestHandler)
    print("=======================================================")
    print(f"  Bullet Brawl Online Game at http://localhost:{PORT}")
    print("=======================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
        server.server_close()
