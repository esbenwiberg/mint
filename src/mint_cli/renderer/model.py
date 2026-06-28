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


def build_prompt(request: RenderRequest, prompt_version: str) -> str:
    lines: list[str] = [
        f"# Render request (prompt {prompt_version})",
        f"Module: {request.module}",
        f"Stack: {request.stack}",
        "",
        "## Definitions",
    ]
    lines += [f"- {d['name']}: {d['text']}" for d in request.definitions] or ["- (none)"]
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
                lines.append(f"- def {d['name']}: {d['text']}")

    if request.required_modules:
        lines += ["", "## Required modules (already generated)"]
        for req in request.required_modules:
            lines.append(f"### {req.get('module')}")
            for f in req.get("files", []):
                lines.append(f"#### {f['path']}")
                language = str(f.get("language") or request.code_fence_language or "text")
                lines.append(f"```{language}")
                lines.append(str(f.get("contents", "")))
                lines.append("```")

    lines += ["", "## Units already rendered"]
    lines += [f"- {u['id']}: {u['title']}" for u in request.units_so_far] or ["- (none)"]

    unit = request.current_unit
    lines += [
        "",
        f"## Implement unit {unit['id']}: {unit['title']}",
        "Spec:",
        *[f"- {s}" for s in unit.get("spec", [])],
        "Acceptance:",
        *[f"- {a}" for a in unit.get("acceptance", [])],
        "",
        f"Phase: {request.phase} (attempt {request.attempt})",
    ]
    if request.feedback:
        lines += [
            "",
            "## Previous attempt failed — fix it. Test output:",
            "```",
            request.feedback.strip(),
            "```",
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
    # First balanced {...}.
    start = text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise MintError("no JSON object found in model output")


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
            result = subprocess.run(
                self.command,
                input=input_text,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=self.timeout_seconds,
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
            command = ["claude", "--print", "--output-format", "text", "--model", model]
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


class AnthropicModelClient:  # pragma: no cover - requires network + key
    """Real provider client. Imported lazily; never used by the test suite."""

    def __init__(self, model: str, api_key: str | None = None, max_tokens: int = 4096) -> None:
        import os

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
        self._client = anthropic.Anthropic(api_key=key)
        self._model = model
        self._max_tokens = max_tokens

    def complete(self, *, system: str, prompt: str, request: RenderRequest) -> str:
        message = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        )
