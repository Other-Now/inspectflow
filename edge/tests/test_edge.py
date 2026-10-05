import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from preprocess import CLASSES, label_from_filename, thumbnail_jpeg, to_tensor  # noqa: E402
from publisher import Publisher  # noqa: E402
from spool import Spool  # noqa: E402
from worker import build_event  # noqa: E402


def test_to_tensor_shape_and_range():
    x = to_tensor(np.full((200, 200), 255, np.uint8))
    assert x.shape == (1, 1, 128, 128) and x.dtype == np.float32
    assert np.allclose(x, 1.0)
    assert np.allclose(to_tensor(np.zeros((200, 200), np.uint8)), -1.0)


def test_label_from_filename_handles_underscored_classes():
    assert CLASSES[label_from_filename("rolled-in_scale_17.jpg")] == "rolled-in_scale"
    assert CLASSES[label_from_filename("pitted_surface_3.jpg")] == "pitted_surface"


def test_thumbnail_is_jpeg():
    assert thumbnail_jpeg(np.zeros((200, 200), np.uint8))[:2] == b"\xff\xd8"


def test_event_id_is_deterministic_per_line_run_seq():
    a = build_event("L1", "r1", 5, 0.0, "patches", 0.5, 1.0, b"x")
    b = build_event("L1", "r1", 5, 9.0, "patches", 0.9, 2.0, b"y")
    c = build_event("L1", "r1", 6, 0.0, "patches", 0.5, 1.0, b"x")
    assert a["id"] == b["id"] != c["id"]
    assert a["needs_review"] and not b["needs_review"]


def test_spool_fifo_ack_and_idempotent_put(tmp_path):
    s = Spool(str(tmp_path / "s.db"))
    for i in range(5):
        s.put(f"id{i}", f"b{i}".encode())
    s.put("id0", b"again")  # re-spool same id: ignored
    rows = s.peek(10)
    assert [r[1] for r in rows] == ["id0", "id1", "id2", "id3", "id4"]
    assert rows[0][2] == b"b0"
    s.ack(rows[0][0])
    assert s.depth() == 4


def test_spool_survives_reopen(tmp_path):
    p = str(tmp_path / "s.db")
    s = Spool(p)
    s.put("a", b"1")
    s.close()  # simulates process exit
    assert Spool(p).depth() == 1


class FlakyPublisher(Publisher):
    """Publisher whose broker accepts `ok` messages, then goes down."""

    def __init__(self, ok):
        self.ok, self.sent = ok, []

    def publish(self, body, msg_id):
        if len(self.sent) >= self.ok:
            return False
        self.sent.append(msg_id)
        return True


def test_drain_stops_at_first_failure_and_keeps_the_rest(tmp_path):
    s = Spool(str(tmp_path / "s.db"))
    for i in range(10):
        s.put(f"id{i}", json.dumps({"i": i}).encode())
    p = FlakyPublisher(ok=4)
    assert p.drain(s) == 4
    assert s.depth() == 6
    assert s.peek(1)[0][1] == "id4"  # oldest unconfirmed is next; order preserved
    p.ok = 100
    assert p.drain(s) == 6 and s.depth() == 0
    assert p.sent == [f"id{i}" for i in range(10)]


def test_unreachable_broker_does_not_block(tmp_path):
    import time
    p = Publisher("amqp://guest:guest@127.0.0.1:1/", backoff_s=10)
    s = Spool(str(tmp_path / "s.db"))
    s.put("a", b"1")
    p.drain(s)  # first attempt fails fast (connection refused)
    t0 = time.perf_counter()
    for _ in range(100):
        p.drain(s)  # inside backoff window: must return immediately
    assert time.perf_counter() - t0 < 0.5
    assert s.depth() == 1
