"""Kōan ollama skill — server status, model management, pulling and removal."""


def _format_size(size_bytes):
    """Format bytes to human-readable size."""
    if not size_bytes:
        return ""
    gb = size_bytes / (1024 ** 3)
    if gb >= 1.0:
        return f"{gb:.1f}GB"
    mb = size_bytes / (1024 ** 2)
    return f"{mb:.0f}MB"


def _format_model_line(m):
    """Format a single model dict as an indented display line."""
    name = m.get("name", m.get("model", "unknown"))
    size = _format_size(m.get("size", 0))
    details = m.get("details", {})
    param_size = details.get("parameter_size", "")
    quant = details.get("quantization_level", "")

    parts = [f"  {name}"]
    if param_size:
        parts.append(f"({param_size})")
    if quant:
        parts.append(f"[{quant}]")
    if size:
        parts.append(size)
    return " ".join(parts)


def _check_provider():
    """Return provider name if Ollama-compatible, else an error string."""
    from app.provider import get_provider_name

    provider = get_provider_name()
    if provider not in ("local", "ollama", "ollama-claude"):
        return None, f"Ollama not active (provider: {provider})"
    return provider, None


def handle(ctx):
    """Handle /ollama command — dispatch to subcommands."""
    args = (ctx.args or "").strip()

    if args in ("help", "-h", "--help"):
        return _handle_help()

    if args.startswith("pull "):
        return _handle_pull(ctx, args[5:].strip())
    if args == "pull":
        return "Usage: /ollama pull <model>\nExample: /ollama pull llama3.3"

    if args.startswith("remove ") or args.startswith("rm "):
        name = args.split(" ", 1)[1].strip()
        return _handle_remove(ctx, name)
    if args in ("remove", "rm"):
        return "Usage: /ollama remove <model>\nExample: /ollama remove llama3.3"

    if args in ("list", "ls", "models"):
        return _handle_list(ctx)

    if args.startswith("show ") or args.startswith("info "):
        name = args.split(" ", 1)[1].strip()
        return _handle_show(ctx, name)
    if args in ("show", "info"):
        return "Usage: /ollama show <model>\nExample: /ollama show llama3.3"

    return _handle_status(ctx)


def _handle_help():
    """Show available /ollama subcommands."""
    return (
        "Ollama management commands:\n"
        "  /ollama           — Server status + models\n"
        "  /ollama list      — List available models\n"
        "  /ollama show <m>  — Detailed model info\n"
        "  /ollama pull <m>  — Download a model\n"
        "  /ollama rm <m>    — Remove a model\n"
        "  /ollama help      — This message\n"
        "\nAliases: /llama, list→ls, show→info, remove→rm"
    )


def _handle_pull(ctx, model_name):
    """Pull a model from the Ollama registry with progress tracking."""
    from app.ollama_client import is_model_available, is_server_ready, pull_model_streaming

    provider, err = _check_provider()
    if err:
        return err

    if not model_name:
        return "Usage: /ollama pull <model>\nExample: /ollama pull llama3.3"

    if not is_server_ready():
        return "Ollama server not responding. Start with: ollama serve"

    if is_model_available(model_name):
        return f"Model '{model_name}' is already available locally."

    # Track progress for the final message
    progress_state = {"last_pct": -1, "last_status": ""}

    def _on_progress(status, completed, total):
        progress_state["last_status"] = status
        if total > 0:
            progress_state["last_pct"] = int(completed * 100 / total)

    ok, detail = pull_model_streaming(model_name, on_progress=_on_progress)
    if ok:
        pct = progress_state["last_pct"]
        size_info = f" ({pct}%)" if pct > 0 else ""
        return f"Model '{model_name}' pulled successfully{size_info}."
    return f"Failed to pull '{model_name}': {detail}"


def _handle_remove(ctx, model_name):
    """Remove a locally stored model."""
    from app.ollama_client import delete_model, get_model_info, is_server_ready

    provider, err = _check_provider()
    if err:
        return err

    if not model_name:
        return "Usage: /ollama remove <model>\nExample: /ollama remove llama3.3"

    if not is_server_ready():
        return "Ollama server not responding. Start with: ollama serve"

    # Show what's about to be deleted
    info = get_model_info(model_name)
    size_str = ""
    if info:
        size_str = f" ({_format_size(info.get('size', 0))})"

    ok, detail = delete_model(model_name)
    if ok:
        return f"Model '{model_name}'{size_str} removed."
    return f"Failed to remove '{model_name}': {detail}"


def _handle_show(ctx, model_name):
    """Show detailed model information via /api/show."""
    from app.ollama_client import format_model_details, is_server_ready

    provider, err = _check_provider()
    if err:
        return err

    if not model_name:
        return "Usage: /ollama show <model>\nExample: /ollama show llama3.3"

    if not is_server_ready():
        return "Ollama server not responding."

    return format_model_details(model_name)


def _handle_list(ctx):
    """Show a compact model list without full server status."""
    from app.ollama_client import is_server_ready, list_models

    provider, err = _check_provider()
    if err:
        return err

    if not is_server_ready():
        return "Ollama server not responding."

    models = list_models()
    if not models:
        return "No models available. Run: /ollama pull <model>"

    lines = [f"Models ({len(models)}):"]
    for m in models:
        lines.append(_format_model_line(m))

    return "\n".join(lines)


def _handle_status(ctx):
    """Show server status and models."""
    from app.ollama_client import (
        get_version,
        is_server_ready,
        list_models,
        list_running_models,
    )

    provider, err = _check_provider()
    if err:
        return err

    lines = []

    # Server health
    ready = is_server_ready()
    if not ready:
        lines.append("Ollama server: not responding")
        lines.append("  Start with: ollama serve")
        return "\n".join(lines)

    version = get_version() or "unknown"
    lines.append(f"Ollama server: running (v{version})")

    # Available models
    models = list_models()
    if not models:
        lines.append("\nNo models pulled. Run: /ollama pull <model>")
        return "\n".join(lines)

    lines.append(f"\nModels ({len(models)}):")
    for m in models:
        lines.append(_format_model_line(m))

    # Running models
    running = list_running_models()
    if running:
        names = [r.get("name", r.get("model", "?")) for r in running]
        lines.append(f"\nLoaded: {', '.join(names)}")

    # Show configured model
    try:
        configured = None
        if provider == "ollama-claude":
            from app.provider.ollama_claude import OllamaClaudeProvider
            p = OllamaClaudeProvider()
            configured = p._get_model()
        else:
            from app.provider.local import LocalLLMProvider
            p = LocalLLMProvider()
            configured = p._get_default_model()
        if configured:
            from app.ollama_client import is_model_available
            available = is_model_available(configured)
            status = "ready" if available else "not pulled"
            lines.append(f"\nConfigured model: {configured} ({status})")
            if not available:
                lines.append(f"  Run: /ollama pull {configured}")
    except Exception:
        pass

    return "\n".join(lines)
