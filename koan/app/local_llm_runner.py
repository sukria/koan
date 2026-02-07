"""
Local LLM agentic runner for Kōan.

Provides a simple agentic loop that calls a local LLM via OpenAI-compatible
API and executes tool calls (read, write, edit, grep, glob, shell).

Supports any server exposing /v1/chat/completions:
- Ollama (http://localhost:11434/v1)
- llama.cpp server (http://localhost:8080/v1)
- LM Studio (http://localhost:1234/v1)
- vLLM (http://localhost:8000/v1)

Usage as CLI:
    python3 -m app.local_llm_runner --prompt "..." --model "..." --base-url "..."

The runner handles:
1. Sending the prompt + system context to the LLM
2. Parsing tool_calls from the response (OpenAI function calling format)
3. Executing tools locally and feeding results back
4. Repeating until the LLM produces a final text response or max_turns is hit
5. Outputting the result to stdout (plain text or JSON)
"""

import argparse
import glob as glob_module
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function calling format)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative file path"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file (creates or overwrites).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "content": {"type": "string", "description": "Content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace a string in a file with new content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "old_string": {"type": "string", "description": "Text to find"},
                    "new_string": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files matching a glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern (e.g., '**/*.py')"},
                    "path": {"type": "string", "description": "Base directory (default: cwd)"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents with a regex pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "path": {"type": "string", "description": "Directory or file to search (default: cwd)"},
                    "file_glob": {"type": "string", "description": "File pattern filter (e.g., '*.py')"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "Execute a shell command and return its output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                },
                "required": ["command"],
            },
        },
    },
]

# Mapping from canonical Koan tool names to function names
_KOAN_TO_FUNCTION = {
    "Read": "read_file",
    "Write": "write_file",
    "Edit": "edit_file",
    "Glob": "glob",
    "Grep": "grep",
    "Bash": "shell",
}


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def _execute_tool(name: str, arguments: Dict[str, Any], cwd: str) -> str:
    """Execute a tool and return its output as a string."""
    try:
        if name == "read_file":
            path = _resolve_path(arguments["path"], cwd)
            if not os.path.isfile(path):
                return f"Error: file not found: {path}"
            content = Path(path).read_text(encoding="utf-8", errors="replace")
            if len(content) > 50000:
                content = content[:50000] + "\n... (truncated)"
            return content

        elif name == "write_file":
            path = _resolve_path(arguments["path"], cwd)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            Path(path).write_text(arguments["content"], encoding="utf-8")
            return f"Written {len(arguments['content'])} chars to {path}"

        elif name == "edit_file":
            path = _resolve_path(arguments["path"], cwd)
            if not os.path.isfile(path):
                return f"Error: file not found: {path}"
            content = Path(path).read_text(encoding="utf-8")
            old = arguments["old_string"]
            new = arguments["new_string"]
            if old not in content:
                return f"Error: old_string not found in {path}"
            count = content.count(old)
            if count > 1:
                return f"Error: old_string matches {count} locations in {path}. Be more specific."
            content = content.replace(old, new, 1)
            Path(path).write_text(content, encoding="utf-8")
            return f"Edited {path}: replaced 1 occurrence"

        elif name == "glob":
            base = arguments.get("path", cwd)
            base = _resolve_path(base, cwd)
            pattern = arguments["pattern"]
            matches = sorted(glob_module.glob(os.path.join(base, pattern), recursive=True))
            if len(matches) > 200:
                matches = matches[:200]
                return "\n".join(matches) + f"\n... ({len(matches)}+ matches, truncated)"
            return "\n".join(matches) if matches else "No matches found"

        elif name == "grep":
            pattern = arguments["pattern"]
            path = _resolve_path(arguments.get("path", cwd), cwd)
            file_glob = arguments.get("file_glob", "")
            cmd = ["grep", "-rn", "--include", file_glob, pattern, path] if file_glob else [
                "grep", "-rn", pattern, path
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
            output = result.stdout
            if len(output) > 20000:
                output = output[:20000] + "\n... (truncated)"
            return output if output else "No matches found"

        elif name == "shell":
            command = arguments["command"]
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=120, cwd=cwd,
            )
            output = result.stdout
            if result.stderr:
                output += "\nSTDERR:\n" + result.stderr
            if len(output) > 30000:
                output = output[:30000] + "\n... (truncated)"
            if not output.strip():
                output = f"(exit code {result.returncode})"
            return output

        else:
            return f"Error: unknown tool '{name}'"

    except subprocess.TimeoutExpired:
        return f"Error: tool '{name}' timed out"
    except Exception as e:
        return f"Error executing {name}: {e}"


def _resolve_path(path: str, cwd: str) -> str:
    """Resolve a path relative to cwd if not absolute."""
    if os.path.isabs(path):
        return path
    return os.path.join(cwd, path)


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

def _call_api(
    base_url: str,
    model: str,
    messages: List[Dict],
    tools: Optional[List[Dict]] = None,
    api_key: str = "",
    temperature: float = 0.0,
) -> Dict:
    """Call OpenAI-compatible chat completions API.

    Uses urllib to avoid requiring the openai package.
    """
    import urllib.request
    import urllib.error

    url = f"{base_url.rstrip('/')}/chat/completions"

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API error {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Cannot connect to {base_url}. Is the LLM server running? Error: {e.reason}"
        ) from e


# ---------------------------------------------------------------------------
# Agentic loop
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an AI coding assistant. You have access to tools for reading, writing, and editing files, searching codebases, and running shell commands.

When you need to perform an action, use the available tools. When you have completed the task or have a response ready, respond with your final answer as plain text (no tool calls).

Be concise and direct. Focus on completing the task efficiently."""


def _filter_tools(
    allowed: Optional[List[str]] = None,
    disallowed: Optional[List[str]] = None,
) -> List[Dict]:
    """Filter tool definitions based on allowed/disallowed lists.

    Args use Koan canonical names (Read, Write, Edit, Glob, Grep, Bash).
    """
    allowed_funcs = None
    disallowed_funcs = set()

    if allowed:
        allowed_funcs = {_KOAN_TO_FUNCTION.get(t, t.lower()) for t in allowed}
    if disallowed:
        disallowed_funcs = {_KOAN_TO_FUNCTION.get(t, t.lower()) for t in disallowed}

    result = []
    for tool in TOOL_DEFINITIONS:
        func_name = tool["function"]["name"]
        if allowed_funcs is not None and func_name not in allowed_funcs:
            continue
        if func_name in disallowed_funcs:
            continue
        result.append(tool)
    return result


def run_agent(
    prompt: str,
    base_url: str,
    model: str,
    api_key: str = "",
    max_turns: int = 10,
    allowed_tools: Optional[List[str]] = None,
    disallowed_tools: Optional[List[str]] = None,
    output_format: str = "",
    cwd: str = "",
    system_prompt: str = "",
) -> Dict[str, Any]:
    """Run the agentic loop.

    Returns a dict with:
        result: Final text response from the LLM
        input_tokens: Total input tokens used
        output_tokens: Total output tokens used
    """
    if not cwd:
        cwd = os.getcwd()

    tools = _filter_tools(allowed_tools, disallowed_tools)
    # Some local models don't support function calling well.
    # If no tools are available, skip tool definitions entirely.
    use_tools = len(tools) > 0

    sys_prompt = system_prompt or SYSTEM_PROMPT
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": prompt},
    ]

    total_input_tokens = 0
    total_output_tokens = 0

    for turn in range(max_turns):
        try:
            response = _call_api(
                base_url=base_url,
                model=model,
                messages=messages,
                tools=tools if use_tools else None,
                api_key=api_key,
            )
        except RuntimeError as e:
            return {
                "result": f"Error: {e}",
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
            }

        # Track tokens
        usage = response.get("usage", {})
        total_input_tokens += usage.get("prompt_tokens", 0)
        total_output_tokens += usage.get("completion_tokens", 0)

        choice = response.get("choices", [{}])[0]
        message = choice.get("message", {})

        # If the model returned tool calls, execute them
        tool_calls = message.get("tool_calls", [])
        if tool_calls:
            # Add assistant message with tool calls to history
            messages.append(message)

            for tc in tool_calls:
                func = tc.get("function", {})
                func_name = func.get("name", "")
                try:
                    func_args = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    func_args = {}

                tool_result = _execute_tool(func_name, func_args, cwd)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", f"call_{turn}_{func_name}"),
                    "content": tool_result,
                })

            continue  # Next turn — let LLM process tool results

        # No tool calls — this is the final response
        content = message.get("content", "")
        return {
            "result": content,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
        }

    # Max turns reached — return whatever we have
    last_content = ""
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            last_content = msg["content"]
            break

    return {
        "result": last_content or "(max turns reached without final response)",
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Local LLM agentic runner for Koan"
    )
    parser.add_argument("-p", "--prompt", required=True, help="Task prompt")
    parser.add_argument("--model", default="", help="Model name (e.g., glm4:latest)")
    parser.add_argument("--base-url", default="", help="API base URL")
    parser.add_argument("--api-key", default="", help="API key (if required)")
    parser.add_argument("--max-turns", type=int, default=10, help="Max agentic turns")
    parser.add_argument("--allowed-tools", default="", help="Comma-separated allowed tools")
    parser.add_argument("--disallowed-tools", default="", help="Comma-separated disallowed tools")
    parser.add_argument("--output-format", default="", help="Output format (json or empty for text)")
    parser.add_argument("--cwd", default="", help="Working directory")
    parser.add_argument("--system-prompt", default="", help="Custom system prompt")

    args = parser.parse_args()

    # Resolve config from env vars if not provided via args
    base_url = args.base_url or os.environ.get("KOAN_LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
    model = args.model or os.environ.get("KOAN_LOCAL_LLM_MODEL", "")
    api_key = args.api_key or os.environ.get("KOAN_LOCAL_LLM_API_KEY", "")

    if not model:
        print("Error: --model is required (or set KOAN_LOCAL_LLM_MODEL)", file=sys.stderr)
        sys.exit(1)

    allowed = [t.strip() for t in args.allowed_tools.split(",") if t.strip()] if args.allowed_tools else None
    disallowed = [t.strip() for t in args.disallowed_tools.split(",") if t.strip()] if args.disallowed_tools else None

    result = run_agent(
        prompt=args.prompt,
        base_url=base_url,
        model=model,
        api_key=api_key,
        max_turns=args.max_turns,
        allowed_tools=allowed,
        disallowed_tools=disallowed,
        output_format=args.output_format,
        cwd=args.cwd or os.getcwd(),
        system_prompt=args.system_prompt,
    )

    if args.output_format == "json":
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(result.get("result", ""))


if __name__ == "__main__":
    main()
