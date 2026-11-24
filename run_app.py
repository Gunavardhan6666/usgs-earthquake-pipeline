"""
run_app.py
----------
Entry point to start the Flask dashboard server.

Usage:
    python run_app.py
    python run_app.py --port 5001

Then open: http://localhost:5000
"""

import argparse
from app import create_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="USGS Flask Dashboard")
    parser.add_argument("--port", type=int, default=5000, help="Port (default: 5000)")
    parser.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    args = parser.parse_args()

    app = create_app()
    print(f"\n🌍 USGS Dashboard running at http://{args.host}:{args.port}\n")
    app.run(host=args.host, port=args.port, debug=True)
