"""
Integration tests for the Flask-SocketIO server.

DISABLE_CAPTURE=1 makes _capture_loop emit synthetic black frames instead of
calling mss or pyautogui, so these tests run safely in headless/CI environments.
"""
import os
import time
import importlib
import pytest

# Must be set before server.py is imported so _capture_loop takes the safe path
os.environ.setdefault('DISABLE_CAPTURE', '1')
os.environ.setdefault('SECRET_TOKEN', 'test-secret-token')
os.environ.setdefault('PORT', '5099')  # avoid port conflicts

# Import after env vars are in place
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import server
from server import app, socketio, SECRET_TOKEN


@pytest.fixture(autouse=True)
def reset_state():
    """Reset mutable server state between tests."""
    server.active_clients = 0
    server.stop_flag.clear()
    yield
    server.stop_flag.clear()


# ── /health endpoint ──────────────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_returns_200(self):
        client = app.test_client()
        resp = client.get('/health')
        assert resp.status_code == 200

    def test_response_is_json(self):
        client = app.test_client()
        resp = client.get('/health')
        data = resp.get_json()
        assert data is not None

    def test_required_keys_present(self):
        client = app.test_client()
        data = client.get('/health').get_json()
        for key in ('uptime', 'connected_clients', 'current_fps', 'virtual_display_id', 'monitor_res'):
            assert key in data, f"Missing key: {key}"

    def test_uptime_is_non_negative(self):
        client = app.test_client()
        data = client.get('/health').get_json()
        assert data['uptime'] >= 0

    def test_virtual_display_id_is_none(self):
        # Phase D doesn't integrate virtual display — must be null
        client = app.test_client()
        data = client.get('/health').get_json()
        assert data['virtual_display_id'] is None

    def test_connected_clients_is_int(self):
        client = app.test_client()
        data = client.get('/health').get_json()
        assert isinstance(data['connected_clients'], int)

    def test_current_fps_is_positive(self):
        client = app.test_client()
        data = client.get('/health').get_json()
        assert data['current_fps'] > 0


# ── WebSocket authentication ──────────────────────────────────────────────────

class TestWebSocketAuth:
    def test_valid_token_connects(self):
        client = socketio.test_client(app, query_string=f'token={SECRET_TOKEN}')
        assert client.is_connected()
        client.disconnect()

    def test_wrong_token_rejected(self):
        client = socketio.test_client(app, query_string='token=wrong-token')
        assert not client.is_connected()

    def test_missing_token_rejected(self):
        client = socketio.test_client(app, query_string='')
        assert not client.is_connected()

    def test_client_count_increments_on_connect(self):
        server.active_clients = 0
        client = socketio.test_client(app, query_string=f'token={SECRET_TOKEN}')
        assert client.is_connected()
        assert server.active_clients == 1
        client.disconnect()

    def test_client_count_decrements_on_disconnect(self):
        server.active_clients = 0
        client = socketio.test_client(app, query_string=f'token={SECRET_TOKEN}')
        client.disconnect()
        assert server.active_clients == 0


# ── rotate_display event ──────────────────────────────────────────────────────

class TestRotateEvent:
    def _connected_client(self):
        return socketio.test_client(app, query_string=f'token={SECRET_TOKEN}')

    def test_valid_portrait(self):
        client = self._connected_client()
        client.emit('rotate_display', {'orientation': 'portrait'})
        assert server.rotation_state == 'portrait'
        client.disconnect()
        server.rotation_state = 'landscape'

    def test_valid_landscape(self):
        client = self._connected_client()
        server.rotation_state = 'portrait'
        client.emit('rotate_display', {'orientation': 'landscape'})
        assert server.rotation_state == 'landscape'
        client.disconnect()

    def test_invalid_orientation_ignored(self):
        client = self._connected_client()
        original = server.rotation_state
        client.emit('rotate_display', {'orientation': 'upside_down'})
        assert server.rotation_state == original
        client.disconnect()

    def test_non_dict_payload_ignored(self):
        client = self._connected_client()
        original = server.rotation_state
        client.emit('rotate_display', 'portrait')
        assert server.rotation_state == original
        client.disconnect()


# ── Max clients enforcement ───────────────────────────────────────────────────

class TestMaxClients:
    def test_excess_client_rejected(self):
        original_max = server.MAX_CLIENTS
        server.MAX_CLIENTS = 1
        server.active_clients = 0

        c1 = socketio.test_client(app, query_string=f'token={SECRET_TOKEN}')
        c2 = socketio.test_client(app, query_string=f'token={SECRET_TOKEN}')

        assert c1.is_connected()
        assert not c2.is_connected()

        c1.disconnect()
        server.MAX_CLIENTS = original_max
        server.active_clients = 0


# ── Index route ───────────────────────────────────────────────────────────────

class TestIndexRoute:
    def test_returns_200(self):
        client = app.test_client()
        resp = client.get(f'/?token={SECRET_TOKEN}')
        assert resp.status_code == 200

    def test_contains_canvas(self):
        client = app.test_client()
        html = client.get(f'/?token={SECRET_TOKEN}').data.decode()
        assert 'screenCanvas' in html

    def test_token_embedded_in_html(self):
        client = app.test_client()
        html = client.get('/').data.decode()
        assert SECRET_TOKEN in html
