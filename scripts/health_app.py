"""P0 process probes only. No banking or authorization endpoints are implemented."""

import asyncio
import os
import secrets

import uvicorn
from fastapi import FastAPI, Header, HTTPException


def create_app(service: str, *, internal: bool = False) -> FastAPI:
    app = FastAPI(title=f"SafeBank {service} P0", docs_url=None, redoc_url=None)
    path = "/internal/health" if internal else "/health"

    @app.get(path)
    def health(x_service_token: str | None = Header(default=None)) -> dict[str, str]:
        if internal or service == "bank-backend":
            name = "SCHEDULER_SERVICE_TOKEN" if internal else "GATEWAY_SERVICE_TOKEN"
            expected = os.getenv(name)
            if (
                not expected
                or not x_service_token
                or not secrets.compare_digest(x_service_token, expected)
            ):
                raise HTTPException(401, "Service credentials required")
        return {"service": service, "status": "ok", "stage": "P0-skeleton"}

    return app


async def main() -> None:
    service = os.environ["SERVICE_NAME"]
    bindings = [(create_app(service), os.getenv("BIND", "0.0.0.0"), int(os.getenv("PORT", "8100")))]
    if service == "gateway":
        public = os.environ["GATEWAY_PUBLIC_BIND"]
        private = os.environ["GATEWAY_INTERNAL_BIND"]
        if public == private or "0.0.0.0" in (public, private):
            raise ValueError("Gateway must bind distinct network interfaces")
        bindings = [
            (create_app(service), public, 8200),
            (create_app(service, internal=True), private, 8201),
        ]
    servers = [
        uvicorn.Server(uvicorn.Config(app, host=host, port=port)) for app, host, port in bindings
    ]
    await asyncio.gather(*(server.serve() for server in servers))


if __name__ == "__main__":
    asyncio.run(main())
