#!/usr/bin/env python3
"""Sirve Veyra (API + interfaz) en una o varias direcciones a la vez.

    python scripts/serve.py --host 10.0.0.4,100.64.0.4 --port 8899

uvicorn solo acepta una dirección por proceso; aquí se abren los sockets y se le entregan ya enlazados.
"""
import argparse
import socket
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.main import app  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1", help="direcciones separadas por coma")
    ap.add_argument("--port", type=int, default=8899)
    a = ap.parse_args()
    socks = []
    for h in [x.strip() for x in a.host.split(",") if x.strip()]:
        s = socket.socket(socket.AF_INET6 if ":" in h else socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((h, a.port))
        s.set_inheritable(True)
        socks.append(s)
    uvicorn.Server(uvicorn.Config(app, log_level="info")).run(sockets=socks)


if __name__ == "__main__":
    main()
