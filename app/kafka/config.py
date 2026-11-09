import os
from collections.abc import Mapping
from dataclasses import dataclass

SECURITY_PROTOCOLS = ("PLAINTEXT", "SSL")


@dataclass(frozen=True)
class KafkaSettings:
    bootstrap_servers: str
    security_protocol: str = "PLAINTEXT"
    client_id: str = "moyeota-ai"


def kafka_settings(env: Mapping[str, str] = os.environ) -> KafkaSettings | None:
    servers = env.get("KAFKA_BOOTSTRAP_SERVERS", "").strip()
    if not servers:
        return None
    protocol = env.get("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT").strip().upper() or "PLAINTEXT"
    if protocol not in SECURITY_PROTOCOLS:
        raise ValueError(f"KAFKA_SECURITY_PROTOCOL must be one of {SECURITY_PROTOCOLS}: {protocol}")
    return KafkaSettings(bootstrap_servers=servers, security_protocol=protocol)
