"""The deterministic, offline renderer.

It selects a template set (by the spec's optional ``template`` key, falling back to
the module name) and emits a file patch for the current unit slice. Output depends
only on the request, so it is fully repeatable — ideal for tests and CI without any
network or API key. It deliberately ignores ``feedback``: a deterministic renderer
cannot "try something different", so a real failure surfaces immediately.
"""

from __future__ import annotations

import json

from ..errors import MintError
from ..hashing import canonical_json
from .base import RenderOutcome, RenderRequest
from .templates import get_template, known_templates


class DeterministicRenderer:
    name = "deterministic"

    def render(self, request: RenderRequest) -> RenderOutcome:
        key = request.template or request.module
        builder = get_template(key)
        if builder is None:
            raise MintError(
                f"Deterministic renderer has no template '{key}' for module "
                f"'{request.module}'. Known templates: {', '.join(known_templates())}. "
                f"Fix: add a 'template:' key to the spec frontmatter, or use the model "
                f"renderer (renderer.provider: model) for free-form specs."
            )
        files = builder(request)
        patch = {
            "summary": f"deterministic render of {request.current_unit_id} "
            f"({request.phase}) via template '{key}'",
            "files": files,
        }
        # The "response" is the canonical patch JSON so the audit trail is uniform
        # across renderers even though there is no model call.
        response = json.dumps(patch, indent=2, sort_keys=True)
        prompt = (
            f"[deterministic] template={key} module={request.module} "
            f"unit={request.current_unit_id} phase={request.phase} "
            f"fingerprint={canonical_json(request.current_unit)[:120]}"
        )
        return RenderOutcome(
            patch=patch,
            renderer=self.name,
            prompt=prompt,
            response=response,
            classification="rendered",
        )
