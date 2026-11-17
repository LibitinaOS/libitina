from dataclasses import dataclass
from typing import Any


class NWSError(Exception):
    """Base error for the NWS client."""


@dataclass
class NWSProblemDetail:
    """Structured error response from the NWS API."""

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    correlationId: str | None = None
    raw: dict[str, Any] | None = None


class NWSHTTPError(NWSError):
    """An unsuccessful response from the NWS API."""

    def __init__(
        self,
        *,
        status_code: int,
        url: str,
        message: str,
        problem: NWSProblemDetail | None = None,
        response_text: str | None = None,
    ):
        self.status_code = status_code
        self.url = url
        self.message = message
        self.problem = problem
        self.response_text = response_text
        super().__init__(f"HTTP {status_code} from {url}: {message}")
