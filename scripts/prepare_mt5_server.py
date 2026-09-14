"""Search an already running dedicated builder; never supply trading credentials."""
import argparse
import json
from app.collector.server_builder import ServerBuilder
from app.collector.windows_inventory import InventoryError

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--builder', required=True)
    parser.add_argument('--company', required=True)
    parser.add_argument('--server', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(ServerBuilder().search(args.builder, args.company, args.server)))
    except InventoryError as error:
        print(json.dumps({'state': 'PREPARATION_FAILED', 'errorCode': str(error)}))
        raise SystemExit(1)

if __name__ == '__main__':
    main()
