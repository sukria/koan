"""Tests for local_llm_runner.py — local LLM agentic loop."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.local_llm_runner import (
    _execute_tool,
    _filter_tools,
    _resolve_path,
    run_agent,
    TOOL_DEFINITIONS,
    _KOAN_TO_FUNCTION,
)


# ---------------------------------------------------------------------------
# Tool execution tests
# ---------------------------------------------------------------------------

class TestExecuteTool:
    """Tests for _execute_tool()."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        # Create a test file
        self.test_file = os.path.join(self.tmpdir, "test.txt")
        Path(self.test_file).write_text("line 1\nline 2\nline 3\n")

    def test_read_file(self):
        result = _execute_tool("read_file", {"path": self.test_file}, self.tmpdir)
        assert "line 1" in result
        assert "line 2" in result

    def test_read_file_not_found(self):
        result = _execute_tool("read_file", {"path": "/nonexistent/file.txt"}, self.tmpdir)
        assert "Error" in result

    def test_read_file_relative(self):
        result = _execute_tool("read_file", {"path": "test.txt"}, self.tmpdir)
        assert "line 1" in result

    def test_write_file(self):
        out_path = os.path.join(self.tmpdir, "output.txt")
        result = _execute_tool("write_file", {"path": out_path, "content": "hello"}, self.tmpdir)
        assert "Written" in result
        assert Path(out_path).read_text() == "hello"

    def test_write_file_creates_dirs(self):
        out_path = os.path.join(self.tmpdir, "sub", "dir", "file.txt")
        result = _execute_tool("write_file", {"path": out_path, "content": "deep"}, self.tmpdir)
        assert "Written" in result
        assert Path(out_path).read_text() == "deep"

    def test_edit_file(self):
        result = _execute_tool(
            "edit_file",
            {"path": self.test_file, "old_string": "line 2", "new_string": "LINE TWO"},
            self.tmpdir,
        )
        assert "replaced" in result.lower() or "Edited" in result
        content = Path(self.test_file).read_text()
        assert "LINE TWO" in content
        assert "line 2" not in content

    def test_edit_file_not_found(self):
        result = _execute_tool(
            "edit_file",
            {"path": "/nonexistent.txt", "old_string": "x", "new_string": "y"},
            self.tmpdir,
        )
        assert "Error" in result

    def test_edit_file_string_not_found(self):
        result = _execute_tool(
            "edit_file",
            {"path": self.test_file, "old_string": "nonexistent", "new_string": "y"},
            self.tmpdir,
        )
        assert "not found" in result.lower()

    def test_edit_file_ambiguous(self):
        # Write a file with duplicate content
        dup_file = os.path.join(self.tmpdir, "dup.txt")
        Path(dup_file).write_text("aaa\naaa\n")
        result = _execute_tool(
            "edit_file",
            {"path": dup_file, "old_string": "aaa", "new_string": "bbb"},
            self.tmpdir,
        )
        assert "2 locations" in result

    def test_glob(self):
        # Create some files
        Path(os.path.join(self.tmpdir, "a.py")).write_text("")
        Path(os.path.join(self.tmpdir, "b.py")).write_text("")
        Path(os.path.join(self.tmpdir, "c.txt")).write_text("")
        result = _execute_tool("glob", {"pattern": "*.py"}, self.tmpdir)
        assert "a.py" in result
        assert "b.py" in result
        assert "c.txt" not in result

    def test_glob_no_matches(self):
        result = _execute_tool("glob", {"pattern": "*.xyz"}, self.tmpdir)
        assert "No matches" in result

    def test_glob_with_path(self):
        result = _execute_tool("glob", {"pattern": "*.txt", "path": self.tmpdir}, self.tmpdir)
        assert "test.txt" in result

    def test_grep(self):
        result = _execute_tool("grep", {"pattern": "line 2", "path": self.tmpdir}, self.tmpdir)
        assert "line 2" in result

    def test_grep_no_matches(self):
        result = _execute_tool("grep", {"pattern": "nonexistent_xyz", "path": self.tmpdir}, self.tmpdir)
        assert "No matches" in result

    def test_shell(self):
        result = _execute_tool("shell", {"command": "echo hello world"}, self.tmpdir)
        assert "hello world" in result

    def test_shell_stderr(self):
        result = _execute_tool("shell", {"command": "echo err >&2"}, self.tmpdir)
        assert "err" in result

    def test_shell_exit_code(self):
        result = _execute_tool("shell", {"command": "true"}, self.tmpdir)
        assert "exit code 0" in result

    def test_unknown_tool(self):
        result = _execute_tool("unknown_tool", {}, self.tmpdir)
        assert "unknown tool" in result.lower()

    def test_shell_timeout(self):
        result = _execute_tool("shell", {"command": "sleep 200"}, self.tmpdir)
        assert "timed out" in result.lower() or "Error" in result


class TestResolvePath:
    """Tests for _resolve_path()."""

    def test_absolute_path(self):
        assert _resolve_path("/foo/bar", "/cwd") == "/foo/bar"

    def test_relative_path(self):
        assert _resolve_path("file.txt", "/cwd") == "/cwd/file.txt"

    def test_relative_nested(self):
        assert _resolve_path("sub/file.txt", "/cwd") == "/cwd/sub/file.txt"


# ---------------------------------------------------------------------------
# Tool filtering
# ---------------------------------------------------------------------------

class TestFilterTools:
    """Tests for _filter_tools()."""

    def test_no_filter_returns_all(self):
        result = _filter_tools()
        assert len(result) == len(TOOL_DEFINITIONS)

    def test_allowed_filters(self):
        result = _filter_tools(allowed=["Read", "Grep"])
        names = {t["function"]["name"] for t in result}
        assert names == {"read_file", "grep"}

    def test_disallowed_filters(self):
        result = _filter_tools(disallowed=["Bash", "Write", "Edit"])
        names = {t["function"]["name"] for t in result}
        assert "shell" not in names
        assert "write_file" not in names
        assert "edit_file" not in names
        assert "read_file" in names

    def test_empty_allowed_returns_none(self):
        result = _filter_tools(allowed=[])
        # Empty list = no filter (None check)
        assert len(result) == len(TOOL_DEFINITIONS)

    def test_koan_tool_mapping(self):
        assert _KOAN_TO_FUNCTION["Read"] == "read_file"
        assert _KOAN_TO_FUNCTION["Write"] == "write_file"
        assert _KOAN_TO_FUNCTION["Edit"] == "edit_file"
        assert _KOAN_TO_FUNCTION["Glob"] == "glob"
        assert _KOAN_TO_FUNCTION["Grep"] == "grep"
        assert _KOAN_TO_FUNCTION["Bash"] == "shell"


# ---------------------------------------------------------------------------
# API client mocking
# ---------------------------------------------------------------------------

def _make_api_response(content="", tool_calls=None, input_tokens=100, output_tokens=50):
    """Helper to build a mock API response."""
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens},
    }


def _make_tool_call(name, arguments, call_id="call_1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


# ---------------------------------------------------------------------------
# Agentic loop tests
# ---------------------------------------------------------------------------

class TestRunAgent:
    """Tests for run_agent() — the core agentic loop."""

    @patch("app.local_llm_runner._call_api")
    def test_simple_text_response(self, mock_api):
        """LLM returns text directly, no tool use."""
        mock_api.return_value = _make_api_response(content="Hello, this is my answer.")
        result = run_agent(
            prompt="Say hello",
            base_url="http://localhost:11434/v1",
            model="test-model",
        )
        assert result["result"] == "Hello, this is my answer."
        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 50

    @patch("app.local_llm_runner._call_api")
    def test_tool_use_then_response(self, mock_api):
        """LLM calls a tool, then produces final answer."""
        tmpdir = tempfile.mkdtemp()
        test_file = os.path.join(tmpdir, "hello.txt")
        Path(test_file).write_text("Hello from file!")

        # First call: LLM requests to read the file
        # Second call: LLM gives final answer
        mock_api.side_effect = [
            _make_api_response(
                tool_calls=[_make_tool_call("read_file", {"path": test_file})],
                input_tokens=200,
                output_tokens=30,
            ),
            _make_api_response(
                content="The file says: Hello from file!",
                input_tokens=300,
                output_tokens=40,
            ),
        ]

        result = run_agent(
            prompt="Read hello.txt",
            base_url="http://localhost:11434/v1",
            model="test-model",
            cwd=tmpdir,
        )
        assert "Hello from file!" in result["result"]
        assert result["input_tokens"] == 500  # 200 + 300
        assert result["output_tokens"] == 70   # 30 + 40

    @patch("app.local_llm_runner._call_api")
    def test_max_turns_limit(self, mock_api):
        """Agent stops after max_turns."""
        # Every call returns a tool call, never a final answer
        mock_api.return_value = _make_api_response(
            tool_calls=[_make_tool_call("shell", {"command": "echo loop"})],
        )
        result = run_agent(
            prompt="Loop forever",
            base_url="http://localhost:11434/v1",
            model="test-model",
            max_turns=3,
        )
        assert "max turns" in result["result"].lower() or result["result"] == ""
        assert mock_api.call_count == 3

    @patch("app.local_llm_runner._call_api")
    def test_api_error_handling(self, mock_api):
        """Graceful handling of API errors."""
        mock_api.side_effect = RuntimeError("Connection refused")
        result = run_agent(
            prompt="Test error",
            base_url="http://localhost:99999/v1",
            model="test-model",
        )
        assert "Error" in result["result"]
        assert "Connection refused" in result["result"]

    @patch("app.local_llm_runner._call_api")
    def test_multi_tool_calls(self, mock_api):
        """LLM issues multiple tool calls in one turn."""
        tmpdir = tempfile.mkdtemp()
        Path(os.path.join(tmpdir, "a.txt")).write_text("file a")
        Path(os.path.join(tmpdir, "b.txt")).write_text("file b")

        mock_api.side_effect = [
            _make_api_response(
                tool_calls=[
                    _make_tool_call("read_file", {"path": os.path.join(tmpdir, "a.txt")}, "call_a"),
                    _make_tool_call("read_file", {"path": os.path.join(tmpdir, "b.txt")}, "call_b"),
                ],
            ),
            _make_api_response(content="Both files read successfully."),
        ]

        result = run_agent(
            prompt="Read both files",
            base_url="http://localhost:11434/v1",
            model="test-model",
            cwd=tmpdir,
        )
        assert "Both files read" in result["result"]

    @patch("app.local_llm_runner._call_api")
    def test_tool_filtering_applied(self, mock_api):
        """Allowed tools filter is respected in API calls."""
        mock_api.return_value = _make_api_response(content="Done")
        run_agent(
            prompt="Test",
            base_url="http://localhost:11434/v1",
            model="test-model",
            allowed_tools=["Read", "Grep"],
        )
        # Check the tools passed to the API
        call_kwargs = mock_api.call_args
        tools = call_kwargs.kwargs.get("tools") or call_kwargs[1].get("tools")
        if tools:
            names = {t["function"]["name"] for t in tools}
            assert names == {"read_file", "grep"}

    @patch("app.local_llm_runner._call_api")
    def test_no_tools_when_empty_filter(self, mock_api):
        """When all tools are disallowed, no tools are passed."""
        mock_api.return_value = _make_api_response(content="No tools needed")
        run_agent(
            prompt="Just answer",
            base_url="http://localhost:11434/v1",
            model="test-model",
            disallowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
        )
        call_kwargs = mock_api.call_args
        tools = call_kwargs.kwargs.get("tools") or call_kwargs[1].get("tools")
        assert tools is None  # Empty list → use_tools is False → None passed

    @patch("app.local_llm_runner._call_api")
    def test_write_tool_execution(self, mock_api):
        """LLM can write files via tool calls."""
        tmpdir = tempfile.mkdtemp()
        out_path = os.path.join(tmpdir, "output.txt")

        mock_api.side_effect = [
            _make_api_response(
                tool_calls=[_make_tool_call("write_file", {"path": out_path, "content": "created!"})],
            ),
            _make_api_response(content="File written."),
        ]

        result = run_agent(
            prompt="Create output.txt",
            base_url="http://localhost:11434/v1",
            model="test-model",
            cwd=tmpdir,
        )
        assert Path(out_path).read_text() == "created!"

    @patch("app.local_llm_runner._call_api")
    def test_invalid_tool_args_json(self, mock_api):
        """Handles malformed JSON in tool arguments gracefully."""
        mock_api.side_effect = [
            {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "call_bad",
                            "function": {"name": "read_file", "arguments": "not json"},
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 50, "completion_tokens": 10},
            },
            _make_api_response(content="Recovered."),
        ]
        result = run_agent(
            prompt="Test bad args",
            base_url="http://localhost:11434/v1",
            model="test-model",
        )
        # Should not crash — the tool error is fed back to the LLM
        assert result["result"] == "Recovered."


# ---------------------------------------------------------------------------
# CLI entry point tests
# ---------------------------------------------------------------------------

class TestCLI:
    """Tests for the CLI entry point."""

    @patch("app.local_llm_runner.run_agent")
    def test_cli_json_output(self, mock_run):
        """--output-format json prints JSON to stdout."""
        mock_run.return_value = {
            "result": "test output",
            "input_tokens": 100,
            "output_tokens": 50,
        }
        import io
        from contextlib import redirect_stdout
        from app.local_llm_runner import main

        out = io.StringIO()
        with patch("sys.argv", [
            "local_llm_runner",
            "-p", "test prompt",
            "--model", "test-model",
            "--base-url", "http://localhost:11434/v1",
            "--output-format", "json",
        ]):
            with redirect_stdout(out):
                main()

        output = json.loads(out.getvalue())
        assert output["result"] == "test output"
        assert output["input_tokens"] == 100

    @patch("app.local_llm_runner.run_agent")
    def test_cli_text_output(self, mock_run):
        """Default output is plain text."""
        mock_run.return_value = {"result": "plain text response"}
        import io
        from contextlib import redirect_stdout
        from app.local_llm_runner import main

        out = io.StringIO()
        with patch("sys.argv", [
            "local_llm_runner",
            "-p", "test prompt",
            "--model", "test-model",
            "--base-url", "http://localhost:11434/v1",
        ]):
            with redirect_stdout(out):
                main()

        assert out.getvalue().strip() == "plain text response"

    def test_cli_missing_model_exits(self):
        """Missing model causes exit."""
        with patch("sys.argv", ["local_llm_runner", "-p", "test"]):
            with patch.dict("os.environ", {}, clear=False):
                os.environ.pop("KOAN_LOCAL_LLM_MODEL", None)
                with pytest.raises(SystemExit):
                    from app.local_llm_runner import main
                    main()

    @patch("app.local_llm_runner.run_agent")
    def test_cli_env_var_defaults(self, mock_run):
        """Env vars provide defaults for base_url and model."""
        mock_run.return_value = {"result": "ok"}
        import io
        from contextlib import redirect_stdout
        from app.local_llm_runner import main

        out = io.StringIO()
        with patch.dict("os.environ", {
            "KOAN_LOCAL_LLM_BASE_URL": "http://custom:8080/v1",
            "KOAN_LOCAL_LLM_MODEL": "custom-model",
            "KOAN_LOCAL_LLM_API_KEY": "secret",
        }):
            with patch("sys.argv", ["local_llm_runner", "-p", "test"]):
                with redirect_stdout(out):
                    main()

        # Verify run_agent was called with env var values
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs["base_url"] == "http://custom:8080/v1"
        assert call_kwargs.kwargs["model"] == "custom-model"
        assert call_kwargs.kwargs["api_key"] == "secret"


# ---------------------------------------------------------------------------
# Tool definition completeness
# ---------------------------------------------------------------------------

class TestToolDefinitions:
    """Verify tool definitions are well-formed."""

    def test_all_tools_have_required_fields(self):
        for tool in TOOL_DEFINITIONS:
            assert tool["type"] == "function"
            func = tool["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func
            assert func["parameters"]["type"] == "object"

    def test_all_koan_tools_mapped(self):
        """Every Koan canonical tool has a mapping."""
        for tool in ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]:
            assert tool in _KOAN_TO_FUNCTION

    def test_all_mapped_tools_exist(self):
        """Every mapped function name exists in tool definitions."""
        defined_names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
        for func_name in _KOAN_TO_FUNCTION.values():
            assert func_name in defined_names
