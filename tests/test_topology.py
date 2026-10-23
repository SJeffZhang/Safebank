from pathlib import Path

import yaml


def test_compose_has_only_frontend_host_port_and_two_gateway_interfaces():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text())
    services = compose["services"]
    assert set(compose["networks"]) == {"agent-net", "bank-net"}
    assert set(services["gateway"]["networks"]) == {"agent-net", "bank-net"}
    for name in ("agent", "frontend", "postgres-agent"):
        assert set(services[name]["networks"]) == {"agent-net"}
    for name in ("bank-backend", "postgres-bank"):
        assert set(services[name]["networks"]) == {"bank-net"}
    for name, service in services.items():
        if name != "frontend":
            assert "ports" not in service
    assert services["frontend"]["ports"] == ["127.0.0.1:3000:3000"]
    assert (
        services["gateway"]["environment"]["GATEWAY_PUBLIC_BIND"]
        != services["gateway"]["environment"]["GATEWAY_INTERNAL_BIND"]
    )
