"""Run inventory reporting against a configured Nest node; never start terminals."""

import argparse
import json
import os
import time

from app.collector.node_agent import AgentError, InventoryAgent, load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--inspect-ui", action="store_true", help="Explicit idle-terminal maintenance: inspect the Server dropdown once.")
    args = parser.parse_args()
    if args.inspect_ui and not args.once:
        parser.error("--inspect-ui requires --once; background heartbeats never open dialogs")
    try:
        config = load_config(args.config)
        agent = InventoryAgent(config, os.environ.get("MT5_AGENT_TOKEN", ""))
        agent.connect()
        while True:
            started = time.monotonic()
            results = [agent.report_slot(slot, args.inspect_ui) for slot in config["slots"]]
            print(json.dumps({"ok": True, "slots": results}), flush=True)
            if args.once:
                return 0
            time.sleep(max(1, 30 - (time.monotonic() - started)))
    except AgentError as exc:
        print(json.dumps({"ok": False, "errorCode": str(exc)}), flush=True)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
