"""Прочитать один результат из parser.results."""
import asyncio
import json
import sys

import aio_pika


async def main():
    conn = await aio_pika.connect_robust("amqp://guest:guest@localhost:5672/")
    ch = await conn.channel()
    q = await ch.declare_queue("parser.results.storage", durable=True)

    msg = await q.get(timeout=5)
    if msg:
        await msg.ack()
        data = json.loads(msg.body.decode())
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print("Очередь пуста")

    await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)