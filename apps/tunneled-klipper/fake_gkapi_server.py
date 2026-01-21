import socket
import threading
import json

HOST = '0.0.0.0'
PORT = 18086

class FakeGkapiServer:
    def __init__(self):
        self.ready_counter = 0

    def handle_client(self, conn):
        with conn:
            buffer = b""
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                buffer += data
                while b"\x03" in buffer:
                    line, buffer = buffer.split(b"\x03", 1)
                    try:
                        req = json.loads(line.decode(errors='ignore'))
                    except Exception:
                        continue
                    # Simulate Query/K3cInfo readiness transition
                    if req.get("method") == "Query/K3cInfo":
                        self.ready_counter += 1
                        if self.ready_counter < 5:
                            resp = {
                                "jsonrpc": "2.0",
                                "id": req.get("id"),
                                "error": {"code": -32000, "message": "not ready"}
                            }
                        else:
                            resp = {
                                "jsonrpc": "2.0",
                                "id": req.get("id"),
                                "result": {"state": "ready"}
                            }
                        conn.sendall((json.dumps(resp) + "\x03").encode())
                    else:
                        # Generic empty result for other methods
                        resp = {
                            "jsonrpc": "2.0",
                            "id": req.get("id"),
                            "result": {}
                        }
                        conn.sendall((json.dumps(resp) + "\x03").encode())

    def run(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((HOST, PORT))
            s.listen()
            print(f"Fake gkapi server listening on {HOST}:{PORT}")
            while True:
                conn, _ = s.accept()
                threading.Thread(target=self.handle_client, args=(conn,), daemon=True).start()

if __name__ == "__main__":
    FakeGkapiServer().run()
