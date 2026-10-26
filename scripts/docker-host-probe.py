"""Operator-only, bounded probe of Docker Desktop -> host loopback connectivity."""
import http.server
import json
from pathlib import Path
import subprocess
import threading


def main():
    config = json.loads(Path("config/local.json").read_text(encoding="utf-8"))
    port = config["transport"]["port"]
    marker = "qq-character-loopback-probe"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(marker.encode())

        def log_message(self, *_args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    script = ("fetch('http://host.docker.internal:" + str(port) +
              "',{signal:AbortSignal.timeout(8000)}).then(r=>r.text())"
              ".then(t=>{if(t!==" + json.dumps(marker) +
              ")throw Error('unexpected_response');console.log('loopback_reachable')})"
              ".catch(e=>{console.error(e.name);process.exitCode=1})")
    try:
        result = subprocess.run([
            config["runtime"]["docker_command"], "exec", "qq-character-runtime",
            "node", "-e", script,
        ], capture_output=True, text=True, timeout=20)
        report = {"host": "127.0.0.1", "port": port,
                  "container_host": "host.docker.internal", "passed": result.returncode == 0,
                  "result": result.stdout.strip(), "error": result.stderr.strip()}
        Path(".state/docker-host-probe.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
