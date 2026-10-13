"""Seed the isolated demo and serve its web workspace with one Python command."""
import argparse
import os
import socket
import sys

import uvicorn

from core.config import LOCAL_DATA_DIR
from seed_connection_demo import main as seed_demo


def main():
    parser = argparse.ArgumentParser(description='Run the local Flyji demo. Normal flight data is preserved.')
    parser.add_argument('--port', type=int, default=8013, help='loopback port (default: 8013)')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    try:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', args.port))
    except OSError as error:
        print(f'Cannot use port {args.port}: {error.strerror}. Choose another --port.', file=sys.stderr)
        return 2
    seed_demo()
    os.environ['FLIGHT_DATABASE_PATH'] = str(LOCAL_DATA_DIR / 'connections-demo.db')
    print(f'\nFlyji demo: http://127.0.0.1:{args.port}/', flush=True)
    uvicorn.run('server.api:app', host='127.0.0.1', port=args.port)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
