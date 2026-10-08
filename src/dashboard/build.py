"""`python -m src.dashboard.build [--out PATH] [--serve PORT]`: build the static dashboard from real artifacts."""

import argparse
import functools
import http.server
from datetime import UTC, datetime
from pathlib import Path

from .render import render_html
from .viewmodel import build_viewmodel


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="src.dashboard.build")
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default="artifacts/dashboard/index.html")
    ap.add_argument("--serve", type=int, default=None, help="serve on 127.0.0.1:PORT after building")
    a = ap.parse_args(argv)
    out = Path(a.root) / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(build_viewmodel(Path(a.root), datetime.now(UTC))), encoding="utf-8")
    print(f"dashboard written: {out}")
    if a.serve:
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out.parent))
        print(f"http://127.0.0.1:{a.serve}/index.html")
        http.server.ThreadingHTTPServer(("127.0.0.1", a.serve), handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
