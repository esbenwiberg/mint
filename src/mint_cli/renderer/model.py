"""Model-backed renderer.

The renderer is decoupled from any provider via the :class:`ModelClient` protocol:
``complete(system, prompt, request) -> str``. Tests inject a scripted client that
returns canned patch JSON, so the whole path is exercised with **no network and no
API key**. Thin live clients are provided for Anthropic's API and local model
CLIs, but they are only reached through the explicit live-record path.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from typing import Any, Callable, Protocol

from ..errors import MintError
from .cassettes import cassette_id
from .base import RenderOutcome, RenderRequest
from .patch import validate_patch

SYSTEM_PROMPT = (
    "You are a code renderer in a regenerative coding system. You implement exactly "
    "one functional unit. Respond with ONLY a single JSON object matching this schema:\n"
    '{"summary": "<short>", "files": [{"path": "<rel path>", "action": "write|delete", '
    '"contents": "<file text, for write>", "root": "module|conformance"}]}\n'
    "Do not include prose, markdown fences, or explanations outside the JSON."
)


class ModelClient(Protocol):
    def complete(self, *, system: str, prompt: str, request: RenderRequest) -> str:  # pragma: no cover
        ...


class ModelOutputError(MintError):
    """Model output failed before it could become a valid patch."""

    def __init__(
        self,
        message: str,
        *,
        prompt: str,
        response: str,
        cassette_id: str | None,
        renderer: str = "model",
    ) -> None:
        super().__init__(message)
        self.prompt = prompt
        self.response = response
        self.cassette_id = cassette_id
        self.renderer = renderer


class ModelRenderer:
    name = "model"

    def __init__(
        self,
        client: ModelClient,
        prompt_version: str = "v1",
        *,
        max_response_chars: int = 200000,
        model: str | None = None,
    ) -> None:
        self._client = client
        self.prompt_version = prompt_version
        self.max_response_chars = max_response_chars
        self.model = model

    def render(self, request: RenderRequest) -> RenderOutcome:
        prompt = build_prompt(request, self.prompt_version)
        response = self._client.complete(
            system=SYSTEM_PROMPT, prompt=prompt, request=request
        )
        c_id = cassette_id(
            prompt_version=self.prompt_version,
            request=request,
            prompt=prompt,
            model=self.model,
        )
        if len(response) > self.max_response_chars:
            raise ModelOutputError(
                "Model renderer response exceeded "
                f"{self.max_response_chars} characters for "
                f"{request.current_unit_id} ({request.phase}). "
                "Return a smaller JSON patch.",
                prompt=prompt,
                response=response,
                cassette_id=c_id,
                renderer=self.name,
            )
        try:
            raw = extract_json(response)
        except MintError as exc:
            raise ModelOutputError(
                f"Model renderer returned unparseable output for "
                f"{request.current_unit_id} ({request.phase}): {exc}"
                "\nReturn only the JSON patch object matching the schema.",
                prompt=prompt,
                response=response,
                cassette_id=c_id,
                renderer=self.name,
            ) from exc
        try:
            patch = validate_patch(raw)
        except MintError as exc:
            raise ModelOutputError(
                f"Model renderer returned an invalid patch for "
                f"{request.current_unit_id} ({request.phase}): {exc}"
                "\nReturn only the JSON patch object matching the schema.",
                prompt=prompt,
                response=response,
                cassette_id=c_id,
                renderer=self.name,
            ) from exc
        return RenderOutcome(
            patch=patch,
            renderer=self.name,
            prompt=prompt,
            response=response,
            cassette_id=c_id,
            classification="rendered",
        )


# Feedback is embedded verbatim in the prompt, which is then hashed into the
# cassette id. Cap it (tail-truncate) as a guard against unbounded test output,
# BEFORE normalization, so the truncate-then-normalize order is deterministic no
# matter whether the engine already truncated upstream.
MAX_FEEDBACK_CHARS = 20000

# Lines that pytest / vitest emit with embedded environment, plugin, and tool
# versions — pure noise for the model and nondeterministic across machines.
_NOISE_HEADER_PREFIXES = (
    "platform ",
    "rootdir:",
    "plugins:",
    "cachedir:",
    "configfile:",
    "RUN v",
    "DEV v",
)
# Durations like "in 0.03s", "1.2s", "12ms".
_DURATION_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|seconds?|milliseconds?)\b")
# Absolute POSIX paths (require a non-word char before the leading slash so "1/2"
# style expressions are left alone) and Windows drive paths.
_UNIX_PATH_RE = re.compile(r"(?<![\w])(?:/[\w.\-+@]+)+/?")
_WIN_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s:'\"()]+")
# Log/date timestamps like "2026-07-02 10:33:01,123" or "2026-07-02T10:33:01.4".
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?")
# Object memory addresses like "<Foo object at 0x104f3a2b0>".
_ADDR_RE = re.compile(r"0x[0-9a-fA-F]+")
# pytest-xdist worker ids like "[gw3]".
_XDIST_WORKER_RE = re.compile(r"\[gw\d+\]")


def _require(mapping: Any, key: str, where: str) -> Any:
    """Loud-failure dict access: a malformed IR fails with an actionable message."""
    try:
        return mapping[key]
    except (KeyError, TypeError) as exc:
        raise MintError(
            f"Render request {where} is missing required field '{key}'."
        ) from exc


def _truncate_tail(text: str, limit: int) -> str:
    """Keep the tail of ``text`` (where the actual failure usually is)."""
    if len(text) <= limit:
        return text
    marker = "...[feedback truncated]...\n"
    return marker + text[-(limit - len(marker)) :]


def _is_noise_header(line: str) -> bool:
    stripped = line.strip()
    return any(stripped.startswith(prefix) for prefix in _NOISE_HEADER_PREFIXES)


def normalize_feedback(feedback: str) -> str:
    """Scrub nondeterministic bits from test output so recorded retries replay.

    Strips durations, redacts absolute/tmp paths to stable placeholders, and drops
    platform/version header lines. Applied as the FINAL transform before feedback is
    embedded in the prompt, so the cassette id is stable regardless of clock, machine,
    or caller. Idempotent and safe to run on an already-truncated string.
    """
    kept: list[str] = []
    for line in feedback.splitlines():
        if _is_noise_header(line):
            continue
        # Timestamps first: strip them before path/duration passes so a date like
        # 2026-07-02 can't be partially eaten by the path regex.
        line = _TIMESTAMP_RE.sub("<TIMESTAMP>", line)
        line = _WIN_PATH_RE.sub("<PATH>", line)
        line = _UNIX_PATH_RE.sub("<PATH>", line)
        line = _DURATION_RE.sub("<DURATION>", line)
        line = _ADDR_RE.sub("<ADDR>", line)
        line = _XDIST_WORKER_RE.sub("[gw]", line)
        kept.append(line.rstrip())
    return "\n".join(kept).strip()


def _code_fence(text: str) -> str:
    """Return a backtick fence guaranteed to be longer than any run in ``text``.

    Prevents test output (or file contents) that contains ``` from breaking out of
    the fenced block — a prompt-injection / malformed-prompt vector.
    """
    longest = run = 0
    for ch in text:
        if ch == "`":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return "`" * max(3, longest + 1)


def build_prompt(request: RenderRequest, prompt_version: str) -> str:
    lines: list[str] = [
        f"# Render request (prompt {prompt_version})",
        f"Module: {request.module}",
        f"Stack: {request.stack}",
        "",
        "## Definitions",
    ]
    lines += [
        f"- {_require(d, 'name', 'definition')}: {_require(d, 'text', 'definition')}"
        for d in request.definitions
    ] or ["- (none)"]
    lines += ["", "## Implementation requirements"]
    lines += [f"- {item}" for item in request.implementation] or ["- (none)"]
    lines += ["", "## Test requirements"]
    lines += [f"- {item}" for item in request.test] or ["- (none)"]

    if request.prompt_hints:
        lines += ["", "## Stack adapter guidance"]
        lines += [f"- {item}" for item in request.prompt_hints]

    if request.imported_context:
        lines += ["", "## Imported context"]
        for ctx in request.imported_context:
            lines.append(f"### from {ctx.get('module')}")
            for d in ctx.get("definitions", []):
                name = _require(d, "name", "imported definition")
                text = _require(d, "text", "imported definition")
                lines.append(f"- def {name}: {text}")

    if request.required_modules:
        lines += ["", "## Required modules (already generated)"]
        for req in request.required_modules:
            lines.append(f"### {req.get('module')}")
            for f in req.get("files", []):
                lines.append(f"#### {_require(f, 'path', 'required module file')}")
                language = str(f.get("language") or request.code_fence_language or "text")
                contents = str(f.get("contents", ""))
                fence = _code_fence(contents)
                lines.append(f"{fence}{language}")
                lines.append(contents)
                lines.append(fence)

    if request.module_files_so_far:
        lines += ["", "## Current module files (already rendered for earlier units)"]
        lines.append(
            "Extend and stay consistent with this code; reuse its exact public names."
        )
        for f in request.module_files_so_far:
            lines.append(f"#### {_require(f, 'path', 'current module file')}")
            language = str(f.get("language") or request.code_fence_language or "text")
            contents = str(f.get("contents", ""))
            fence = _code_fence(contents)
            lines.append(f"{fence}{language}")
            lines.append(contents)
            lines.append(fence)

    lines += ["", "## Units already rendered"]
    lines += [
        f"- {_require(u, 'id', 'rendered unit')}: {_require(u, 'title', 'rendered unit')}"
        for u in request.units_so_far
    ] or ["- (none)"]

    unit = request.current_unit
    unit_id = _require(unit, "id", "current_unit")
    unit_title = _require(unit, "title", "current_unit")
    lines += [
        "",
        f"## Implement unit {unit_id}: {unit_title}",
        "Spec:",
        *[f"- {s}" for s in unit.get("spec", [])],
        "Acceptance:",
        *[f"- {a}" for a in unit.get("acceptance", [])],
    ]
    if request.unit_resources:
        lines += [
            "",
            "## Unit resources (verbatim project files)",
            "Treat these as authoritative inputs where the unit spec references them.",
        ]
        for f in request.unit_resources:
            lines.append(f"#### {_require(f, 'path', 'unit resource')}")
            contents = str(f.get("contents", ""))
            fence = _code_fence(contents)
            lines.append(fence)
            lines.append(contents)
            lines.append(fence)
    lines += [
        "",
        f"Phase: {request.phase} (attempt {request.attempt})",
    ]
    if request.feedback:
        # Truncate-then-normalize: cap first (composes with any upstream truncation),
        # then scrub nondeterministic bits as the final step so the cassette id is
        # stable. The fence adapts to the (normalized) content so ``` inside the test
        # output cannot break the block.
        feedback = normalize_feedback(_truncate_tail(request.feedback, MAX_FEEDBACK_CHARS))
        fence = _code_fence(feedback)
        lines += [
            "",
            "## Previous attempt failed — fix it. Test output:",
            fence,
            feedback,
            fence,
        ]
    lines += ["", "Return the JSON patch now."]
    return "\n".join(lines)


def extract_json(text: str) -> dict[str, Any]:
    """Best-effort extraction of a single JSON object from model output."""
    text = text.strip()
    # Direct parse.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fenced ```json ... ``` block.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    # First balanced {...}, string-aware. A `}` inside a JSON string value must not
    # close the object early, and a parse failure on one candidate must not abort the
    # scan — advance to the next depth-0 `{` and try again.
    start = text.find("{")
    while start != -1:
        end = _find_matching_brace(text, start)
        if end is not None:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        start = text.find("{", start + 1)
    raise MintError("no JSON object found in model output")


def _find_matching_brace(text: str, start: int) -> int | None:
    """Index of the `}` that closes the `{` at ``start``, honoring quoted strings.

    Skips over characters inside double-quoted strings (respecting backslash escapes)
    so braces embedded in string values don't affect the depth count. Returns ``None``
    if the braces never balance.
    """
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


class ScriptedModelClient:
    """A fully offline mock client for tests.

    ``responses`` may be:
      * a callable ``(request) -> str`` — full control, or
      * a dict keyed by ``"<unit>:<phase>:<attempt>"`` with ``"<unit>:<phase>"`` and
        ``"default"`` fallbacks.
    """

    def __init__(self, responses: Callable[[RenderRequest], str] | dict[str, str]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, int]] = []

    def complete(self, *, system: str, prompt: str, request: RenderRequest) -> str:
        self.calls.append((request.current_unit_id, request.phase, request.attempt))
        if callable(self._responses):
            return self._responses(request)
        keys = [
            f"{request.current_unit_id}:{request.phase}:{request.attempt}",
            f"{request.current_unit_id}:{request.phase}",
            f"{request.current_unit_id}",
            "default",
        ]
        for key in keys:
            if key in self._responses:
                return self._responses[key]
        raise MintError(
            f"ScriptedModelClient has no response for {keys[0]} "
            f"(known keys: {', '.join(sorted(self._responses))})"
        )


class CliModelClient:
    """Run a non-interactive model CLI and read the JSON patch from stdout."""

    def __init__(
        self,
        *,
        name: str,
        command: list[str],
        timeout_seconds: int | None = None,
    ) -> None:
        if not command:
            raise MintError(f"{name} command is empty.")
        self.name = name
        self.command = command
        self.timeout_seconds = timeout_seconds or _cli_timeout_seconds()

    def complete(self, *, system: str, prompt: str, request: RenderRequest) -> str:
        executable = self.command[0]
        if shutil.which(executable) is None:
            raise MintError(
                f"{self.name} executable not found: {executable!r}. "
                f"Install it, put it on PATH, or set {self._env_hint()}."
            )
        input_text = _cli_input(system=system, prompt=prompt)
        try:
            # Agentic CLIs can execute file tools even when asked to only print a
            # JSON patch — `claude --print` was observed (three separate times)
            # writing stale module copies into the project root, outside the patch
            # roots. Running in an empty scratch cwd guarantees any stray write
            # lands in a throwaway directory, never in the user's repo, and keeps
            # the render hermetic (no repo-level CLI config or context leaks in).
            with tempfile.TemporaryDirectory(prefix="mint-model-cli-") as scratch:
                result = subprocess.run(
                    self.command,
                    input=input_text,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=self.timeout_seconds,
                    cwd=scratch,
                )
        except subprocess.TimeoutExpired as exc:
            raise MintError(
                f"{self.name} command timed out after {self.timeout_seconds}s "
                f"for {request.current_unit_id} ({request.phase})."
            ) from exc
        except OSError as exc:
            raise MintError(f"{self.name} command failed to start: {exc}") from exc

        if result.returncode != 0:
            detail = _tail(result.stderr.strip() or result.stdout.strip())
            raise MintError(
                f"{self.name} command failed with exit code {result.returncode}."
                + (f"\n{detail}" if detail else "")
            )
        response = result.stdout.strip()
        if not response:
            raise MintError(f"{self.name} command produced no stdout.")
        return response

    def _env_hint(self) -> str:
        return "the provider-specific MINT_*_CLI_COMMAND override"


class ClaudeCliModelClient(CliModelClient):
    """Live client for Claude Code's non-interactive print mode."""

    def __init__(self, model: str) -> None:
        command = _command_from_env("MINT_CLAUDE_CLI_COMMAND")
        if command is None:
            # --tools "" disables every built-in tool: patch generation needs pure
            # text output, and an agent with file tools is the stray-patch bug
            # waiting to happen (see CliModelClient.complete's scratch cwd).
            command = [
                "claude",
                "--print",
                "--output-format",
                "text",
                "--tools",
                "",
                "--model",
                model,
            ]
        super().__init__(name="Claude CLI", command=command)

    def _env_hint(self) -> str:
        return "MINT_CLAUDE_CLI_COMMAND"


class CodexCliModelClient(CliModelClient):
    """Live client for Codex CLI's non-interactive exec mode."""

    def __init__(self, model: str) -> None:
        command = _command_from_env("MINT_CODEX_CLI_COMMAND")
        if command is None:
            command = [
                "codex",
                "exec",
                "--model",
                model,
                "--sandbox",
                "read-only",
                "--ask-for-approval",
                "never",
                "--color",
                "never",
                "-",
            ]
        super().__init__(name="Codex CLI", command=command)

    def _env_hint(self) -> str:
        return "MINT_CODEX_CLI_COMMAND"


def _command_from_env(name: str) -> list[str] | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    try:
        return shlex.split(value)
    except ValueError as exc:
        raise MintError(f"{name} is not a valid shell-style command: {exc}") from exc


def _cli_timeout_seconds() -> int:
    value = os.environ.get("MINT_CLI_MODEL_TIMEOUT_SECONDS")
    if value is None or not value.strip():
        return 1800
    try:
        seconds = int(value)
    except ValueError as exc:
        raise MintError("MINT_CLI_MODEL_TIMEOUT_SECONDS must be an integer.") from exc
    if seconds <= 0:
        raise MintError("MINT_CLI_MODEL_TIMEOUT_SECONDS must be greater than 0.")
    return seconds


def _cli_input(*, system: str, prompt: str) -> str:
    return (
        "# System instructions\n"
        f"{system.strip()}\n\n"
        "# Render request\n"
        f"{prompt.strip()}\n"
    )


def _tail(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return "..." + text[-limit:]


# Multi-file JSON patches can approach ``max_response_chars`` (200KB ≈ tens of
# thousands of tokens). 4096 silently truncated real patches and poisoned cassettes;
# default high and stream so we never trip the SDK's non-streaming timeout guard.
DEFAULT_ANTHROPIC_MAX_TOKENS = 32000
# Explicit overall request timeout and retry count so a hung/slow API call can't
# stall a render indefinitely, rather than silently inheriting whatever the SDK
# default happens to be. Both overridable via env for slow links / large patches.
DEFAULT_ANTHROPIC_TIMEOUT_SECONDS = 600.0
DEFAULT_ANTHROPIC_MAX_RETRIES = 2


def _anthropic_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise MintError(f"{name} must be a number, got {raw!r}.") from exc
    if value <= 0:
        raise MintError(f"{name} must be greater than 0, got {value}.")
    return value


def _anthropic_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise MintError(f"{name} must be an integer, got {raw!r}.") from exc
    if value < 0:
        raise MintError(f"{name} must be >= 0, got {value}.")
    return value


class AnthropicModelClient:  # pragma: no cover - requires network + key
    """Real provider client. Imported lazily; never used by the test suite."""

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_ANTHROPIC_MAX_TOKENS,
    ) -> None:
        try:
            import anthropic  # type: ignore
        except ImportError as exc:
            raise MintError(
                "The 'anthropic' package is required for the live model renderer. "
                "Install it with: pip install anthropic"
            ) from exc
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise MintError(
                "ANTHROPIC_API_KEY is not set. Export it or pass api_key, or use the "
                "deterministic renderer (renderer.provider: local) for offline runs."
            )
        if max_tokens <= 0:
            raise MintError("Anthropic max_tokens must be greater than 0.")
        timeout = _anthropic_float_env(
            "MINT_ANTHROPIC_TIMEOUT_SECONDS", DEFAULT_ANTHROPIC_TIMEOUT_SECONDS
        )
        max_retries = _anthropic_int_env(
            "MINT_ANTHROPIC_MAX_RETRIES", DEFAULT_ANTHROPIC_MAX_RETRIES
        )
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(
            api_key=key, timeout=timeout, max_retries=max_retries
        )
        self._model = model
        self._max_tokens = max_tokens

    def complete(self, *, system: str, prompt: str, request: RenderRequest) -> str:
        # Stream so a large max_tokens doesn't trip the SDK's non-streaming HTTP
        # timeout guard, and wrap every SDK failure in MintError for the loud-failure
        # contract. get_final_message() gives us the full Message with stop_reason.
        try:
            with self._client.messages.stream(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                message = stream.get_final_message()
        except self._anthropic.AnthropicError as exc:
            raise MintError(
                f"Anthropic API call failed for {request.current_unit_id} "
                f"({request.phase}): {exc}"
            ) from exc

        # A truncated response is garbage; fail loudly so RecordingClient never
        # persists it as a valid cassette.
        if getattr(message, "stop_reason", None) == "max_tokens":
            raise MintError(
                f"Anthropic response for {request.current_unit_id} ({request.phase}) "
                f"was truncated at max_tokens={self._max_tokens}. Raise the renderer "
                "max_tokens knob (or reduce the patch size); refusing to record a "
                "truncated cassette."
            )

        text = "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        )
        if not text.strip():
            raise MintError(
                f"Anthropic response for {request.current_unit_id} ({request.phase}) "
                f"contained no text output (stop_reason="
                f"{getattr(message, 'stop_reason', None)!r})."
            )
        return text
