"""Gemini Flash 2.5 client with SQLite-backed cache.

Three uses (per the LLM-usage policy):

  1. Onboarder.draft_spec  -> structured output, JSON Schema = MappingSpec
  2. Ingestor LLM blocks    -> structured output, JSON Schema declared in spec
  3. One-shot self-repair   -> resend pydantic error, get repaired JSON

The cache key is the sha256 of (model | prompt_template | sorted cache_inputs).
Repeat invocations with the same key short-circuit to the stored response.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, TYPE_CHECKING

from backend import config
from backend.ingest.store import IngestStore

# google-genai imports cost ~1s of cold-start time on Windows. Defer them so
# `import backend.ingest` stays fast for tests/CLI paths that never call out.
if TYPE_CHECKING:
    from google.genai import types as _genai_types  # noqa: F401

log = logging.getLogger("qontext.llm")

DEFAULT_MODEL = "gemini-2.5-flash"


class LLMError(RuntimeError):
    """Wraps SDK / parsing failures so callers can decide whether to retry."""


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cache_key_hash(model: str, prompt_template: str, cache_inputs: dict[str, Any]) -> str:
    """Deterministic cache key for a prompt/inputs combination."""
    canonical = json.dumps(cache_inputs, sort_keys=True, separators=(",", ":"), default=str)
    return _hash(f"{model}\x00{prompt_template}\x00{canonical}")


_GEMINI_UNSUPPORTED_KEYWORDS = frozenset({
    # Gemini's response_schema is OpenAPI-flavored, not full JSON Schema.
    "additionalProperties", "discriminator", "$schema", "$id",
    "patternProperties", "unevaluatedProperties", "unevaluatedItems",
    "allOf", "oneOf", "not", "if", "then", "else",
    "title", "examples", "default", "readOnly", "writeOnly",
    "contentMediaType", "contentEncoding",
})


def sanitize_schema_for_gemini(schema: Any, defs: dict[str, Any] | None = None) -> Any:
    """Strip JSON-Schema features Gemini's response_schema rejects, and
    inline `$ref` lookups against `$defs` so the SDK doesn't choke on them.
    """
    if isinstance(schema, dict):
        local_defs = schema.get("$defs") or schema.get("definitions") or {}
        if defs is None:
            defs = {**local_defs}
        else:
            defs = {**defs, **local_defs}
        if "$ref" in schema:
            ref = schema["$ref"]
            # accept "#/$defs/Foo" or "#/definitions/Foo"
            name = ref.rsplit("/", 1)[-1]
            target = defs.get(name)
            if target is None:
                return {"type": "object"}
            return sanitize_schema_for_gemini(target, defs=defs)
        out: dict[str, Any] = {}
        for k, v in schema.items():
            if k in _GEMINI_UNSUPPORTED_KEYWORDS or k in {"$defs", "definitions"}:
                continue
            out[k] = sanitize_schema_for_gemini(v, defs=defs)
        # Gemini wants "type" — if a union (anyOf) was the only constraint,
        # collapse to the first option's type.
        if "anyOf" in schema and "type" not in out:
            options = [
                sanitize_schema_for_gemini(o, defs=defs)
                for o in schema["anyOf"]
                if isinstance(o, dict) and o.get("type") != "null"
            ]
            if options:
                out = options[0]
        return out
    if isinstance(schema, list):
        return [sanitize_schema_for_gemini(x, defs=defs) for x in schema]
    return schema


class GeminiClient:
    def __init__(
        self,
        ingest_store: IngestStore,
        *,
        api_key: str | None = None,
        default_model: str = DEFAULT_MODEL,
    ):
        key = api_key or config.GEMINI_API_KEY
        if not key:
            raise LLMError(
                "GEMINI_API_KEY not set — add it to .env or pass api_key explicitly"
            )
        # Lazy import: paying the ~1s SDK boot only when an LLM client is
        # actually instantiated, not on every `import backend.ingest`.
        from google import genai
        self._client = genai.Client(api_key=key)
        self._cache = ingest_store
        self._default_model = default_model

    def extract_structured(
        self,
        *,
        prompt_template: str,
        prompt_inputs: dict[str, Any],
        output_schema: dict[str, Any],
        cache_inputs: dict[str, Any] | None = None,
        system_instruction: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        bypass_cache: bool = False,
    ) -> tuple[Any, str]:
        """Run a structured-output call, returning (parsed_json, raw_text).

        `prompt_template`: e.g. "Extract mentions from:\n{body}"
        `prompt_inputs`: substituted into the template.
        `cache_inputs`: extra fields to mix into the cache key (default = prompt_inputs).
        `output_schema`: JSON Schema fed to Gemini's response_schema.
        """
        model = model or self._default_model
        cache_inputs = cache_inputs if cache_inputs is not None else prompt_inputs
        ckey = cache_key_hash(model, prompt_template, cache_inputs)

        if not bypass_cache:
            cached = self._cache.llm_cache_get(ckey)
            if cached is not None:
                log.debug("llm cache hit %s model=%s", ckey[:12], model)
                return cached["response"], cached["raw_output"]

        prompt = prompt_template.format(**prompt_inputs)
        sanitized = sanitize_schema_for_gemini(output_schema)
        from google.genai import types
        cfg = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=sanitized,
            system_instruction=system_instruction,
            temperature=temperature,
        )
        try:
            resp = self._client.models.generate_content(
                model=model, contents=prompt, config=cfg,
            )
        except Exception as e:
            raise LLMError(f"gemini call failed: {e}") from e

        raw = resp.text or ""
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise LLMError(f"gemini returned non-JSON despite schema: {raw[:300]}") from e

        self._cache.llm_cache_put(
            cache_key_hash=ckey,
            prompt_hash=_hash(prompt),
            model=model,
            response=parsed,
            raw_output=raw,
        )
        return parsed, raw

    def repair(
        self,
        *,
        prompt_template: str,
        prompt_inputs: dict[str, Any],
        output_schema: dict[str, Any],
        previous_output: Any,
        validator_error: str,
        system_instruction: str | None = None,
        model: str | None = None,
    ) -> tuple[Any, str]:
        """One-shot self-repair: send the validator error back, ask for a fix.

        Always bypasses the cache (the original cache key already pointed at
        the broken output).
        """
        repair_template = (
            "Your previous output failed validation. Fix it.\n\n"
            "PREVIOUS OUTPUT:\n{previous}\n\n"
            "VALIDATOR ERROR:\n{error}\n\n"
            "ORIGINAL PROMPT:\n{original}"
        )
        return self.extract_structured(
            prompt_template=repair_template,
            prompt_inputs={
                "previous": json.dumps(previous_output)[:4000],
                "error": validator_error[:2000],
                "original": prompt_template.format(**prompt_inputs)[:4000],
            },
            output_schema=output_schema,
            cache_inputs={"_repair_of": cache_key_hash(
                model or self._default_model, prompt_template, prompt_inputs,
            )},
            system_instruction=system_instruction,
            model=model,
            bypass_cache=True,
        )
