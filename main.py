import asyncio
import logging
import sys

import betterlogging as bl
from checker_service import Checker
from config import threads
from data_class import DataQueue
from sms_hub_service import SmsHubService

client = SmsHubService()
data_queue = DataQueue()
level = logging.DEBUG
bl.basic_colorized_config(level=level)

if sys.version_info >= (3, 8) and sys.platform.lower().startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def main():
    tasks = []
    for _ in range(threads):
        checker = Checker(data_queue=data_queue)
        tasks.append(checker.checker_worker())

    await asyncio.gather(*tasks)


if __name__ == '__main__':
    asyncio.run(main())
