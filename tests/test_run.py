"""run.py — boş port bulma testleri."""
import socket

from app import config
from run import find_free_port


def test_returns_port_in_range():
    port = find_free_port(config.DEFAULT_PORT)
    assert config.DEFAULT_PORT <= port < config.DEFAULT_PORT + 20


def test_returned_port_is_actually_free():
    port = find_free_port(config.DEFAULT_PORT)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        assert probe.connect_ex((config.HOST, port)) != 0


def test_skips_busy_port():
    busy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    busy.bind((config.HOST, config.DEFAULT_PORT))
    busy.listen(1)
    try:
        assert find_free_port(config.DEFAULT_PORT) != config.DEFAULT_PORT
    finally:
        busy.close()
