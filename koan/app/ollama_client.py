"""Ollama REST API client for Kōan.

Wraps the native Ollama HTTP API (not the OpenAI-compatible /v1 endpoint)
for server management: health checks, model listing, version detection,
model pulling, and model deletion.

The Ollama API runs at http://localhost:11434 by default.
Endpoints used:
  GET    /api/tags     — list available models
  GET    /api/ps       — list running/loaded models
  GET    /api/version  — server version info
  POST   /api/pull     — pull a model (streaming NDJSON progress)
  DELETE /api/delete   — remove a locally stored model
  HEAD   /              — lightweight health probe

Reference: https://github.com/ollama/ollama/blob/main/docs/api.md
"""

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_OLLAMA_HOST = "http://localhost:11434"


def _get_ollama_host(base_url: str = "") -> str:
    """Derive the Ollama host from a base_url or environment.

    The base_url may be an OpenAI-compat URL like http://localhost:11434/v1.
    We strip the /v1 suffix to get the native Ollama API root.

    Falls back to OLLAMA_HOST env var, then DEFAULT_OLLAMA_HOST.
    """
    import os

    if base_url:
        host = base_url.rstrip("/")
        # Strip OpenAI-compat suffix
        for suffix in ("/v1", "/v1/"):
            if host.endswith(suffix.rstrip("/")):
                host = host[: -len(suffix.rstrip("/"))]
                break
        return host

    return os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST)


def _api_request(
    host: str, path: str, method: str = "GET",
    body: Optional[Dict[str, Any]] = None, timeout: float = 5.0,
) -> urllib.request.Request:
    """Build and execute an Ollama API request, returning the raw response.

    This is the shared error-handling layer for all API methods.
    Raises RuntimeError on connection or HTTP errors.
    """
    url = f"{host.rstrip('/')}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")

    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        resp_body = e.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"Ollama API error {e.code} on {path}: {resp_body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Cannot connect to Ollama at {host}: {e.reason}"
        ) from e
    except Exception as e:
        raise RuntimeError(f"Ollama API request failed: {e}") from e


def _api_get(host: str, path: str, timeout: float = 5.0) -> Dict[str, Any]:
    """Perform a GET request to the Ollama API. Returns parsed JSON."""
    with _api_request(host, path, "GET", timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _api_post(host: str, path: str, body: Dict[str, Any], timeout: float = 5.0) -> Dict[str, Any]:
    """Perform a POST request to the Ollama API. Returns parsed JSON."""
    with _api_request(host, path, "POST", body=body, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _api_delete(host: str, path: str, body: Dict[str, Any], timeout: float = 5.0) -> bool:
    """Perform a DELETE request to the Ollama API. Returns True on 2xx."""
    with _api_request(host, path, "DELETE", body=body, timeout=timeout) as resp:
        return 200 <= resp.status < 300


def is_server_ready(base_url: str = "", timeout: float = 3.0) -> bool:
    """Check if the Ollama server is responding.

    Uses a lightweight HEAD request to the root endpoint.
    Returns True if the server responds (any HTTP status), False otherwise.
    """
    host = _get_ollama_host(base_url)
    url = f"{host.rstrip('/')}/"
    req = urllib.request.Request(url, method="HEAD")

    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        # Even a 4xx/5xx means the server is running
        return True
    except Exception:
        return False


def get_version(base_url: str = "", timeout: float = 5.0) -> Optional[str]:
    """Get the Ollama server version string.

    Returns the version (e.g. "0.16.0") or None if unreachable.
    """
    host = _get_ollama_host(base_url)
    try:
        data = _api_get(host, "/api/version", timeout=timeout)
        return data.get("version", None)
    except RuntimeError:
        return None


def list_models(base_url: str = "", timeout: float = 5.0) -> List[Dict[str, Any]]:
    """List all locally available models.

    Returns a list of model dicts with keys like:
        name, model, modified_at, size, digest, details
    Returns empty list if server is unreachable.
    """
    host = _get_ollama_host(base_url)
    try:
        data = _api_get(host, "/api/tags", timeout=timeout)
        return data.get("models", [])
    except RuntimeError:
        return []


def list_running_models(base_url: str = "", timeout: float = 5.0) -> List[Dict[str, Any]]:
    """List currently loaded/running models.

    Returns a list of model dicts with runtime info (size_vram, etc.).
    Returns empty list if server is unreachable.
    """
    host = _get_ollama_host(base_url)
    try:
        data = _api_get(host, "/api/ps", timeout=timeout)
        return data.get("models", [])
    except RuntimeError:
        return []


def is_model_available(model_name: str, base_url: str = "", timeout: float = 5.0) -> bool:
    """Check if a specific model is pulled and available locally.

    Performs a fuzzy match: "qwen2.5-coder:14b" matches "qwen2.5-coder:14b"
    and "qwen2.5-coder" matches "qwen2.5-coder:latest".

    Args:
        model_name: Model name to check (e.g. "qwen2.5-coder:14b").
        base_url: Ollama server URL.
        timeout: Request timeout.

    Returns True if the model is available.
    """
    models = list_models(base_url=base_url, timeout=timeout)
    return _model_matches_any(model_name, models)


def _find_matching_model(
    model_name: str, models: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Find a model in the list by fuzzy name matching.

    Handles Ollama naming conventions:
    - Exact match on 'name' or 'model' field
    - "foo" matches "foo:latest"
    - "foo:tag" matches "foo:tag"

    Returns the model dict if found, None otherwise.
    """
    if not model_name:
        return None

    query = model_name.strip()
    query_with_tag = query if ":" in query else f"{query}:latest"

    for m in models:
        for key in ("name", "model"):
            val = m.get(key, "")
            if not val:
                continue
            if val == query or val == query_with_tag:
                return m
            base = val.split(":")[0]
            if base == query.split(":")[0] and ":" not in query:
                return m
    return None


def _model_matches_any(model_name: str, models: List[Dict[str, Any]]) -> bool:
    """Check if model_name matches any model in the list."""
    return _find_matching_model(model_name, models) is not None


def get_model_info(model_name: str, base_url: str = "", timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    """Get info about a specific model from the local model list.

    Returns the model dict if found, None otherwise.
    """
    models = list_models(base_url=base_url, timeout=timeout)
    return _find_matching_model(model_name, models)


def check_server_and_model(
    model_name: str, base_url: str = "", timeout: float = 5.0,
    auto_pull: bool = False,
) -> Tuple[bool, str]:
    """Combined check: server reachable + model available.

    Args:
        model_name: Model to check (e.g. "llama3.3").
        base_url: Ollama server URL.
        timeout: Request timeout.
        auto_pull: If True, automatically pull the model when not available.

    Returns (ok, detail) where:
        ok=True, detail="" — ready to use
        ok=False, detail="..." — human-readable error message
    """
    host = _get_ollama_host(base_url)

    if not is_server_ready(base_url=base_url, timeout=timeout):
        hint = " (check OLLAMA_HOST or KOAN_LOCAL_LLM_BASE_URL)" if host == DEFAULT_OLLAMA_HOST else ""
        return False, f"Ollama server not responding at {host}{hint}"

    if not model_name:
        return False, "No model configured (set KOAN_LOCAL_LLM_MODEL or local_llm.model in config.yaml)"

    if not is_model_available(model_name, base_url=base_url, timeout=timeout):
        if auto_pull:
            ok, detail = pull_model(model_name, base_url=base_url)
            if ok:
                return True, f"auto-pulled {model_name}"
            return False, f"Auto-pull failed for '{model_name}': {detail}"
        return False, f"Model '{model_name}' not found locally. Run: ollama pull {model_name}"

    return True, ""


def pull_model(
    model_name: str, base_url: str = "", timeout: float = 600.0
) -> Tuple[bool, str]:
    """Pull (download) a model from the Ollama registry.

    Uses the POST /api/pull endpoint with stream=false for simplicity.
    This is a blocking call — large models may take several minutes.

    Args:
        model_name: Model to pull (e.g. "llama3.3", "qwen2.5-coder:14b").
        base_url: Ollama server URL.
        timeout: Request timeout (default 10 min for large models).

    Returns (ok, detail):
        ok=True, detail="success" — model pulled successfully
        ok=False, detail="..." — error message
    """
    if not model_name or not model_name.strip():
        return False, "No model name provided"

    host = _get_ollama_host(base_url)

    if not is_server_ready(base_url=base_url, timeout=5.0):
        return False, f"Ollama server not responding at {host}"

    try:
        result = _api_post(
            host, "/api/pull",
            {"name": model_name.strip(), "stream": False},
            timeout=timeout,
        )
        status = result.get("status", "")
        if "success" in status.lower():
            return True, "success"
        return True, status or "completed"
    except RuntimeError as e:
        return False, str(e)


def delete_model(
    model_name: str, base_url: str = "", timeout: float = 30.0
) -> Tuple[bool, str]:
    """Delete a locally stored model.

    Uses the DELETE /api/delete endpoint.

    Args:
        model_name: Model to delete (e.g. "llama3.3", "qwen2.5-coder:14b").
        base_url: Ollama server URL.
        timeout: Request timeout.

    Returns (ok, detail):
        ok=True, detail="deleted" — model removed successfully
        ok=False, detail="..." — error message
    """
    if not model_name or not model_name.strip():
        return False, "No model name provided"

    host = _get_ollama_host(base_url)

    if not is_server_ready(base_url=base_url, timeout=5.0):
        return False, f"Ollama server not responding at {host}"

    # Verify model exists before attempting deletion
    if not is_model_available(model_name, base_url=base_url, timeout=5.0):
        return False, f"Model '{model_name}' not found locally"

    try:
        _api_delete(
            host, "/api/delete",
            {"name": model_name.strip()},
            timeout=timeout,
        )
        return True, "deleted"
    except RuntimeError as e:
        return False, str(e)


def pull_model_streaming(
    model_name: str, base_url: str = "", timeout: float = 600.0,
    on_progress=None,
) -> Tuple[bool, str]:
    """Pull a model with streaming progress updates.

    Parses NDJSON progress lines from the Ollama /api/pull endpoint.
    Calls on_progress(status, completed, total) for each progress update.

    Args:
        model_name: Model to pull.
        base_url: Ollama server URL.
        timeout: Total request timeout (default 10 min).
        on_progress: Optional callback(status: str, completed: int, total: int).

    Returns (ok, detail):
        ok=True, detail="success" — model pulled successfully
        ok=False, detail="..." — error message
    """
    if not model_name or not model_name.strip():
        return False, "No model name provided"

    host = _get_ollama_host(base_url)

    if not is_server_ready(base_url=base_url, timeout=5.0):
        return False, f"Ollama server not responding at {host}"

    url = f"{host.rstrip('/')}/api/pull"
    data = json.dumps({"name": model_name.strip(), "stream": True}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            last_status = ""
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    progress = json.loads(line)
                except json.JSONDecodeError:
                    continue

                status = progress.get("status", "")
                completed = progress.get("completed", 0)
                total = progress.get("total", 0)
                last_status = status

                if on_progress and callable(on_progress):
                    on_progress(status, completed, total)

            if "success" in last_status.lower():
                return True, "success"
            return True, last_status or "completed"
    except urllib.error.HTTPError as e:
        resp_body = e.read().decode("utf-8", errors="replace")[:200]
        return False, f"Ollama API error {e.code}: {resp_body}"
    except urllib.error.URLError as e:
        return False, f"Cannot connect to Ollama at {host}: {e.reason}"
    except Exception as e:
        return False, f"Pull failed: {e}"


def show_model(
    model_name: str, base_url: str = "", timeout: float = 5.0
) -> Optional[Dict[str, Any]]:
    """Get detailed model information via the /api/show endpoint.

    Returns model metadata including architecture, parameter count,
    quantization, context length, license, and template info.

    Returns None if the model is not found or the server is unreachable.

    Response keys (non-exhaustive):
        modelfile, parameters, template, details, model_info
    The 'details' dict contains: parent_model, format, family,
        families, parameter_size, quantization_level.
    The 'model_info' dict contains architecture-level metadata like
        context_length, embedding_length, etc.
    """
    if not model_name or not model_name.strip():
        return None

    host = _get_ollama_host(base_url)
    try:
        return _api_post(
            host, "/api/show",
            {"name": model_name.strip()},
            timeout=timeout,
        )
    except RuntimeError:
        return None


def format_model_details(model_name: str, base_url: str = "", timeout: float = 5.0) -> str:
    """Format detailed model information for display.

    Calls /api/show and presents key metadata in a readable format.
    Returns a multi-line string suitable for Telegram/console display.
    """
    info = show_model(model_name, base_url=base_url, timeout=timeout)
    if info is None:
        return f"Model '{model_name}' not found."

    lines = [f"Model: {model_name}"]

    details = info.get("details", {})
    if details.get("parameter_size"):
        lines.append(f"  Parameters: {details['parameter_size']}")
    if details.get("family"):
        lines.append(f"  Family: {details['family']}")
    if details.get("quantization_level"):
        lines.append(f"  Quantization: {details['quantization_level']}")
    if details.get("format"):
        lines.append(f"  Format: {details['format']}")

    # Extract context length from model_info if available
    model_info = info.get("model_info", {})
    for key in model_info:
        if "context_length" in key:
            lines.append(f"  Context: {model_info[key]} tokens")
            break

    # Show license snippet if present
    license_text = info.get("license", "")
    if license_text:
        # Show first line only (licenses can be very long)
        first_line = license_text.strip().split("\n")[0][:80]
        lines.append(f"  License: {first_line}")

    return "\n".join(lines)


def format_model_list(base_url: str = "", timeout: float = 5.0) -> str:
    """Format a human-readable list of available models.

    Returns a multi-line string suitable for Telegram/console display.
    """
    models = list_models(base_url=base_url, timeout=timeout)
    if not models:
        return "No models available (is Ollama running?)"

    lines = []
    for m in models:
        name = m.get("name", m.get("model", "unknown"))
        size_bytes = m.get("size", 0)
        size_gb = size_bytes / (1024 ** 3) if size_bytes else 0
        details = m.get("details", {})
        param_size = details.get("parameter_size", "")
        quant = details.get("quantization_level", "")

        parts = [name]
        if param_size:
            parts.append(f"({param_size})")
        if quant:
            parts.append(f"[{quant}]")
        if size_gb >= 0.1:
            parts.append(f"{size_gb:.1f}GB")
        lines.append(" ".join(parts))

    return "\n".join(lines)
