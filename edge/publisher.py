"""RabbitMQ publisher with publisher confirms and non-blocking reconnect.

`drain()` is called after every frame. If the connection is down it returns
immediately (unless the backoff timer has expired, in which case it tries one
reconnect) — inference never waits on the network.
"""
import time

import pika
from pika.exceptions import AMQPError

QUEUE = "inspections"


def declare(ch) -> None:
    ch.queue_declare(queue=QUEUE, durable=True, arguments={"x-queue-type": "quorum"})


class Publisher:
    def __init__(self, url: str, backoff_s: float = 1.0, max_backoff_s: float = 5.0):
        self.params = pika.URLParameters(url)
        self.params.socket_timeout = 2
        self.params.blocked_connection_timeout = 5
        self.params.connection_attempts = 1
        self.conn = None
        self.ch = None
        self.backoff = backoff_s
        self.base_backoff = backoff_s
        self.max_backoff = max_backoff_s
        self.next_try = 0.0
        self.reconnects = 0

    @property
    def up(self) -> bool:
        return self.ch is not None and self.ch.is_open

    def _connect(self) -> bool:
        now = time.monotonic()
        if now < self.next_try:
            return False
        try:
            self.conn = pika.BlockingConnection(self.params)
            self.ch = self.conn.channel()
            self.ch.confirm_delivery()
            declare(self.ch)
            self.backoff = self.base_backoff
            self.reconnects += 1
            return True
        except (AMQPError, OSError):
            self._drop()
            self.next_try = now + self.backoff
            self.backoff = min(self.backoff * 2, self.max_backoff)
            return False

    def _drop(self) -> None:
        try:
            if self.conn is not None and self.conn.is_open:
                self.conn.close()
        except Exception:
            pass
        self.conn = None
        self.ch = None

    def publish(self, body: bytes, msg_id: str) -> bool:
        """One confirmed publish. Returns False (and drops the connection) on failure."""
        if not self.up and not self._connect():
            return False
        try:
            self.ch.basic_publish(
                exchange="", routing_key=QUEUE, body=body,
                properties=pika.BasicProperties(delivery_mode=2, message_id=msg_id,
                                                content_type="application/json"),
                mandatory=True,
            )
            return True
        except (AMQPError, OSError):
            self._drop()
            self.next_try = time.monotonic() + self.backoff
            return False

    def drain(self, spool, budget: int = 200) -> int:
        """Replay spooled events oldest-first. Rows are deleted only once confirmed,
        one DELETE per batch; a crash mid-batch re-sends at most one batch, which
        the cloud dedupes by id."""
        sent = 0
        while sent < budget:
            rows = spool.peek(min(50, budget - sent))
            if not rows:
                break
            last = None
            for n, msg_id, body in rows:
                if not self.publish(body, msg_id):
                    break
                last = n
                sent += 1
            if last is not None:
                spool.ack_through(last)
            if last != rows[-1][0]:
                return sent
        return sent

    def pump(self) -> None:
        """Service heartbeats while idle."""
        if self.up:
            try:
                self.conn.process_data_events(0)
            except (AMQPError, OSError):
                self._drop()

    def close(self) -> None:
        self._drop()
