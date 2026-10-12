import asyncio
import logging
import signal
import sys

import metrics
import rabbitmq
import redpanda
from env import get_env


async def main() -> int:
    broker_type = get_env("BROKER_TYPE", "redpanda")
    if broker_type in ("redpanda", "kafka"):
        run = redpanda.run
    elif broker_type == "rabbitmq":
        run = rabbitmq.run
    else:
        logging.error("unknown BROKER_TYPE=%r, expected redpanda or rabbitmq", broker_type)
        return 1

    metrics.serve_metrics(get_env("METRICS_ADDR", ":2112"))

    # `stop` is our ctx.Done(): signal handlers set it, loops check it.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    await run(stop)
    logging.info("producer exited")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    sys.exit(asyncio.run(main()))
