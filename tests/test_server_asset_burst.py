"""Real queued loopback requests: a desktop page must hydrate under asset fan-out."""
import http.client
import socket
import threading
import unittest
from medagent.runtime import build_runtime
from medagent.server import Handler, MedicalAgentServer


class AssetBurstTests(unittest.TestCase):
    def test_queued_asset_burst_is_served_with_desktop_auth(self):
        runtime = build_runtime(backend="demo")
        context = runtime.start()
        server = MedicalAgentServer(
            ("127.0.0.1", 0), Handler,
            context.services.require("consultation"),
            context.services.require("events"),
            access_token="synthetic-asset-burst-token",
        )
        clients = []
        worker = None
        assets = ["app.js", "execution-focus-ui.js", "execution-focus.css"]
        try:
            # Deliberately queue a full burst before accepting any connection.
            # The old five-connection backlog resets or times out on this burst.
            for index in range(24):
                client = socket.create_connection(server.server_address, timeout=1)
                clients.append(client)
                client.sendall((
                    f"GET /{assets[index % len(assets)]} HTTP/1.0\r\n"
                    "Host: 127.0.0.1\r\n"
                    "X-MedAgent-Token: synthetic-asset-burst-token\r\n\r\n"
                ).encode("ascii"))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            for client in clients:
                client.settimeout(5)
                response = http.client.HTTPResponse(client)
                response.begin()
                self.assertEqual(response.status, 200)
                self.assertGreater(len(response.read()), 100)
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            try:
                connection.request("GET", "/execution-focus-ui.js")
                self.assertEqual(connection.getresponse().status, 401)
            finally:
                connection.close()
        finally:
            for client in clients:
                client.close()
            if worker is not None:
                server.shutdown()
                worker.join(timeout=5)
            server.server_close()
            runtime.stop()


if __name__ == "__main__":
    unittest.main()
