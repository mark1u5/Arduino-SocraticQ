"""Turn text into vectors with the embedding server."""
import math
import time

from .config import CONFIG
from .client import post

URL = f"http://127.0.0.1:{CONFIG['EMBED_PORT']}/v1/embeddings"
# About 3.5 characters per token keeps a text inside the 512-token context
MAX_CHARS = 1400

# Circuit-breaker state to prevent hanging 60s per problem when :8082 is unreachable
_COOLDOWN_SECONDS: float = 30.0
_last_failure_time: float = 0.0
_failure_reason: str = ""
_MAX_BATCH_SIZE: int = 24


def is_circuit_open() -> bool:
    """Return True if the embedding server is currently in cooldown."""
    if _last_failure_time <= 0.0:
        return False
    return (time.monotonic() - _last_failure_time) < _COOLDOWN_SECONDS


def get_cooldown_remaining() -> float:
    """Return seconds remaining in cooldown, or 0.0 if circuit is closed."""
    if _last_failure_time <= 0.0:
        return 0.0
    elapsed = time.monotonic() - _last_failure_time
    return max(0.0, _COOLDOWN_SECONDS - elapsed)


def reset_circuit() -> None:
    """Manually reset the circuit breaker state."""
    global _last_failure_time, _failure_reason
    _last_failure_time = 0.0
    _failure_reason = ""


def set_cooldown(seconds: float) -> None:
    """Configure cooldown duration in seconds."""
    global _COOLDOWN_SECONDS
    _COOLDOWN_SECONDS = max(1.0, float(seconds))


def embed(texts, query=False, timeout=60):
    """Return one unit-length vector per text. Set query=True for search questions.

    Protected by a circuit-breaker: if the server at :8082 fails or is unreachable,
    trips a timed cooldown to fail fast (sub-millisecond) instead of hanging 60s per problem.
    Also automatically chunks batches larger than 24 to avoid HTTP timeouts on QRB2210.
    """
    global _last_failure_time, _failure_reason

    if not texts:
        return []

    # 1. Circuit-breaker check: fail fast if in cooldown
    if is_circuit_open():
        remaining = get_cooldown_remaining()
        raise ConnectionError(
            f"Embedding server (:8082) circuit-breaker OPEN (cooldown active: {remaining:.1f}s remaining; "
            f"last error: {_failure_reason})"
        )

    # 2. Prevent large candidate batch stalls by chunking to at most _MAX_BATCH_SIZE
    if len(texts) > _MAX_BATCH_SIZE:
        all_vectors = []
        for i in range(0, len(texts), _MAX_BATCH_SIZE):
            chunk = texts[i : i + _MAX_BATCH_SIZE]
            all_vectors.extend(embed(chunk, query=query, timeout=timeout))
        return all_vectors

    # 3. Cap per-request HTTP timeout to at most 15.0s so bulk indexing doesn't trip on 5s batches
    eff_timeout = min(float(timeout), 15.0) if timeout else 15.0
    prefix = CONFIG["EMBED_QUERY_PREFIX"] if query else ""
    payload = {"input": [(prefix + t)[:MAX_CHARS] for t in texts]}

    try:
        response = post(URL, payload, timeout=eff_timeout)
        vectors = [item["embedding"] for item in sorted(response["data"], key=lambda d: d["index"])]
        # Successful response closes circuit
        _last_failure_time = 0.0
        _failure_reason = ""
        return [normalize(v) for v in vectors]
    except Exception as exc:
        _last_failure_time = time.monotonic()
        _failure_reason = str(exc)
        raise ConnectionError(
            f"Embedding server (:8082) request failed; circuit-breaker tripped for {_COOLDOWN_SECONDS:.1f}s: {exc}"
        ) from exc


def normalize(vector):
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]

