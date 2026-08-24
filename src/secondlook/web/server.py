"""A stdlib dev harness for the two server-rendered views.

`python -m secondlook.web.server` serves the no-JS Finding Detail and Brief
from the fixture set, so the fallback can be opened, printed and throttled
in a browser without Subsystem L (issue #13) existing.

**This is a development and demo harness, not a deployment target.** It is
`http.server`, single-threaded, no auth, no TLS, loopback by default. When
Subsystem L lands it owns the routes and calls `render.py` directly; this
file becomes redundant and should be deleted rather than hardened.

Responses are gzipped when the client offers it, because the budget in
`docs/performance-budget.md` is stated in gzipped bytes and a harness that
served them uncompressed would flatter every measurement taken against it.
"""

from __future__ import annotations

import argparse
import gzip
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from secondlook.web.fixtures import load_all
from secondlook.web.render import render_brief, render_finding_detail

_BRIEF = re.compile(r"^/cases/([^/]+)/brief/?$")
_FINDING = re.compile(r"^/findings/([^/]+)/?$")


class Handler(BaseHTTPRequestHandler):
    server_version = "secondlook-subsystem-m"

    def _send(self, status: int, html: str) -> None:
        body = html.encode("utf-8")
        headers = [("Content-Type", "text/html; charset=utf-8")]
        if "gzip" in self.headers.get("Accept-Encoding", ""):
            body = gzip.compress(body, 9)
            headers.append(("Content-Encoding", "gzip"))
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        # An error page is still a page: same shell, same stylesheet, and it
        # says what went wrong rather than rendering an empty view that
        # reads as "there is nothing here".
        from secondlook.web.render import page

        self._send(status, page("Not found — Athena", f"<h1>{status}</h1><p>{message}</p>"))

    def do_GET(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's own casing
        data = load_all()
        brief = _BRIEF.match(self.path)
        if brief:
            case = data["case"]
            if brief.group(1) != case["id"]:
                self._error(404, f"No fixture case with id {brief.group(1)}.")
                return
            self._send(200, render_brief(case, data["changes"], data["queue"], data["findings"]))
            return

        finding = _FINDING.match(self.path)
        if finding:
            record = data["findings"].get(finding.group(1))
            if record is None:
                self._error(404, f"No fixture finding with id {finding.group(1)}.")
                return
            self._send(200, render_finding_detail(record))
            return

        self._error(
            404,
            "Server-rendered routes are /cases/&lt;id&gt;/brief and "
            "/findings/&lt;id&gt;. The dashboard and queue are client routes; "
            "run the Vite dev server for those.",
        )

    def log_message(self, fmt: str, *args) -> None:
        print(f"[subsystem-m] {fmt % args}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8014)
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="loopback by default; this harness has no auth and should not be bound wider",
    )
    args = parser.parse_args(argv)

    data = load_all()
    case_id = data["case"]["id"]
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[subsystem-m] http://{args.host}:{args.port}/cases/{case_id}/brief")
    for finding_id in sorted(data["findings"]):
        print(f"[subsystem-m] http://{args.host}:{args.port}/findings/{finding_id}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[subsystem-m] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
