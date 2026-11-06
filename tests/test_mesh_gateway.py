import json
from email.message import Message

import pytest

from agent.mesh.node_gateway import (
    DISCOVERY_REQUEST,
    MeshDiscoveryService,
    MeshGateway,
    MeshGatewayConfigurationError,
)


def _gateway(tmp_path, host="192.168.1.20"):
    return MeshGateway(
        host,
        "a" * 40,
        tmp_path / "server.crt",
        tmp_path / "server.key",
        port=8443,
        node_id_path=tmp_path / "node.json",
    )


def test_mesh_auth_is_private_tls_token_and_route_scoped(tmp_path):
    gateway = _gateway(tmp_path)
    headers = Message()
    assert gateway.authorize("GET", "/api/mesh/telemetry", headers, "192.168.1.5") == (
        False,
        401,
        "A valid mesh bearer token is required.",
    )

    headers["Authorization"] = f"Bearer {'a' * 40}"
    assert gateway.authorize("GET", "/api/mesh/telemetry", headers, "192.168.1.5")[0]
    assert gateway.authorize("GET", "/api/devices", headers, "192.168.1.5")[0] is False
    assert gateway.authorize("POST", "/api/mesh/tasks", headers, "8.8.8.8")[0] is False
    headers["Origin"] = "https://hsx.example"
    headers["Access-Control-Request-Method"] = "POST"
    assert gateway.authorize(
        "OPTIONS",
        "/api/mesh/tasks",
        headers,
        "192.168.1.5",
    )[0] is False


def test_mesh_gateway_rejects_unsafe_listener_configuration(tmp_path):
    with pytest.raises(MeshGatewayConfigurationError, match="private"):
        _gateway(tmp_path, "0.0.0.0")
    with pytest.raises(MeshGatewayConfigurationError, match="32"):
        MeshGateway("192.168.1.20", "short", tmp_path / "cert", tmp_path / "key")


def test_mesh_discovery_advertises_only_private_node_metadata(tmp_path):
    gateway = _gateway(tmp_path)
    discovery = MeshDiscoveryService(gateway)
    response = discovery.response_for(DISCOVERY_REQUEST, "192.168.1.8")
    assert response is not None
    node = json.loads(response)
    assert node["service"] == "hsx"
    assert node["host"] == "192.168.1.20"
    assert node["transport"] == "https"
    assert discovery.response_for(DISCOVERY_REQUEST, "192.168.1.8") is None
    assert discovery.response_for(DISCOVERY_REQUEST, "8.8.8.8") is None
    assert discovery.response_for(b"not-discovery", "192.168.1.8") is None
