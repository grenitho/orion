# backend/app/services/llm_router.py
import asyncio
import os
import random
import re

import litellm

# >>> CRITICAL FIX: _turn_on_debug() used to be ON UNCONDITIONALLY.
# That made litellm print the FULL raw request/response (including the whole
# message history) to stdout on EVERY acompletion() call — and with
# max_iterations there can be hundreds per task, which could flood the stdout
# pipe captured by the parent process (e.g. the VS Code extension). If the
# parent does not read fast enough, the backend can stall waiting for the
# buffer to drain (backpressure), then get force-killed by the parent's
# watchdog/health-check — one of the most likely causes of the random
# "terminal process terminated" reports.
# Now it is only enabled when explicitly set via .env, for manual debugging.
if os.getenv("LITELLM_DEBUG", "false").lower() in ("1", "true", "yes"):
    litellm._turn_on_debug()

from litellm import APIConnectionError, BadRequestError, RateLimitError, Timeout

# ==========================================
# 1. PRIMARY CONFIGURATION: GEMINI (N API KEYS - ROTATION)
# ==========================================
# >>> FIX: this used to hardcode EXACTLY 3 lines (GEMINI_API_KEY_1/2/3) --
# every time the user added/removed keys in .env, this file HAD to be edited
# manually again. Now it automatically scans ALL env vars matching the
# GEMINI_API_KEY_<N> pattern (N = any number, need not be consecutive or
# start at 1), sorted by N, so adding GEMINI_API_KEY_4, _5, etc. to .env
# just works without touching this code again.
_gemini_key_pattern = re.compile(r"^GEMINI_API_KEY_(\d+)$")


def _scan_gemini_keys() -> list:
    """Return every non-empty GEMINI_API_KEY_<N> value, sorted by N.

    Scanning ALL env vars matching the pattern (N = any number, need not be
    consecutive or start at 1) means adding GEMINI_API_KEY_4, _5, etc. to
    .env just works without touching this code again. The scan is also
    repeated when the execution queue is built (UPDATE-011), so tests and
    late .env loads see fresh values.
    """
    found = []
    for env_name, env_value in os.environ.items():
        match = _gemini_key_pattern.match(env_name)
        if match and env_value and env_value.strip():
            found.append((int(match.group(1)), env_value.strip()))
    # Sort by index number (GEMINI_API_KEY_1 first, then _2, etc.) --
    # this order does not affect usage (keys are still randomized per
    # request), it only keeps the logs consistent/predictable.
    found.sort(key=lambda pair: pair[0])
    return [key for _, key in found]


GEMINI_API_KEYS = _scan_gemini_keys()

print(f"[LLM_ROUTER] Detected {len(GEMINI_API_KEYS)} Gemini API keys from .env.")

PRIMARY_MODEL = os.getenv("DEFAULT_MODEL", "gemini/gemini-3.5-flash")

# ==========================================
# 2. FIRST-CLASS PROVIDERS: PROVIDER_<N>_* (UPDATE-011)
# ==========================================
# Generic multi-provider tiers: OpenAI, Anthropic, DeepSeek, Qwen, Groq,
# OpenRouter, Ollama -- anything LiteLLM can talk to, each with its own key.
# One numbered group = one provider account:
#
#   PROVIDER_<N>_NAME         optional label for the logs (default: Provider N)
#   PROVIDER_<N>_API_KEY      REQUIRED -- group skipped (with a warning) if empty
#   PROVIDER_<N>_BASE_URL     optional -- custom OpenAI-compatible endpoint
#   PROVIDER_<N>_MODELS       REQUIRED -- comma separated model list
#   PROVIDER_<N>_TEMPERATURE  optional -- defaults to 0.4
#
# Model naming rule (same as the legacy FALLBACK tier): a name containing
# "/" is handed to LiteLLM verbatim (anthropic/claude-..., deepseek/...);
# a name without "/" gets the openai/ prefix, which is correct for every
# OpenAI-compatible endpoint (including BASE_URL ones).
# Groups are tried in numeric order (1 before 2) and models inside a group
# in the order they were written -- configured order = priority, no shuffle.
_provider_key_pattern = re.compile(
    r"^PROVIDER_([1-9][0-9]?)_(NAME|API_KEY|BASE_URL|MODELS|TEMPERATURE)$"
)
PROVIDER_TEMPERATURE_DEFAULT = 0.4


def _scan_provider_entries() -> list:
    """Collect PROVIDER_<N>_* env groups into an ordered list of dicts."""
    slots = {}
    for env_name, env_value in os.environ.items():
        match = _provider_key_pattern.match(env_name)
        if not match:
            continue
        value = (env_value or "").strip()
        if not value:
            continue
        slots.setdefault(int(match.group(1)), {})[match.group(2).lower()] = value

    entries = []
    for index in sorted(slots):
        slot = slots[index]
        missing = [
            f"PROVIDER_{index}_{field.upper()}"
            for field in ("api_key", "models")
            if not slot.get(field)
        ]
        if missing:
            print(
                f"[LLM_ROUTER] [WARNING] PROVIDER_{index}_* is incomplete "
                f"(missing {', '.join(missing)}); group skipped."
            )
            continue
        models = []
        for raw_model in slot["models"].split(","):
            model = raw_model.strip()
            if model:
                models.append(model if "/" in model else f"openai/{model}")
        if not models:
            print(
                f"[LLM_ROUTER] [WARNING] PROVIDER_{index}_MODELS lists no "
                f"model; group skipped."
            )
            continue
        temperature = PROVIDER_TEMPERATURE_DEFAULT
        raw_temperature = slot.get("temperature")
        if raw_temperature:
            try:
                temperature = float(raw_temperature)
            except ValueError:
                print(
                    f"[LLM_ROUTER] [WARNING] PROVIDER_{index}_TEMPERATURE="
                    f"{raw_temperature!r} is not a number; using "
                    f"{PROVIDER_TEMPERATURE_DEFAULT}."
                )
        name = slot.get("name")
        entries.append(
            {
                "index": index,
                "label": f"{name} (Provider {index})" if name else f"Provider {index}",
                "api_key": slot["api_key"],
                "base_url": slot.get("base_url"),
                "models": models,
                "temperature": temperature,
            }
        )
    return entries


PROVIDER_ENTRIES = _scan_provider_entries()
print(
    f"[LLM_ROUTER] Detected {len(PROVIDER_ENTRIES)} first-class provider "
    f"group(s) from .env."
)

# ==========================================
# 3. LEGACY FALLBACK + CLOUDFLARE (read at queue-build time)
# ==========================================
# Kept verbatim for backwards compatibility with older .env files: one
# shared key + base url + model list (FALLBACK_*) and the Cloudflare AI
# worker (CLOUDFLARE_*). New setups with several providers should prefer
# PROVIDER_<N>_* above; both can coexist, providers are tried first.

# ==========================================
# 4. RETRY & TIMEOUT CONFIGURATION
# ==========================================
TIMEOUT_SEC = int(os.getenv("LLM_TIMEOUT", "60"))
MAX_RETRIES = max(1, int(os.getenv("LLM_MAX_RETRIES_PER_MODEL", "2")))

# >>> PATCH ORION-6b: new configuration (all have safe defaults)
# minimal | low | medium | high   -- use "high" for a coding agent
REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "high").strip().lower()
if REASONING_EFFORT not in {"minimal", "low", "medium", "high"}:
    REASONING_EFFORT = "high"
MAX_OUTPUT_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "32768"))
KIRIM_TEMPERATURE_GEMINI = os.getenv("LLM_SEND_TEMPERATURE", "0").lower() in ("1", "true", "yes")
BACKOFF_BASE = float(os.getenv("LLM_BACKOFF_BASE", "1.5"))
BACKOFF_CAP = float(os.getenv("LLM_BACKOFF_CAP", "20.0"))

# ==========================================
# 5. EXECUTION QUEUE BUILDER (UPDATE-011)
# ==========================================
# >>> CRITICAL FIX (kept from before): temperature=1.0 used to be HARDCODED
# for ALL models on ALL tiers at once. That value is only needed SPECIFICALLY
# for Gemini 3.x (to avoid hang/timeout), while fallback/provider models are
# usually smaller/weaker and turn incoherent when forced that hot. Therefore
# every tier keeps its own temperature, overridable via .env:
#   GEMINI_TEMPERATURE (default 1.0), PROVIDER_<N>_TEMPERATURE (default 0.4),
#   FALLBACK_TEMPERATURE (default 0.4), CLOUDFLARE_TEMPERATURE (default 0.4).
def build_execution_queue() -> list:
    """Assemble the try-order of every configured model.

    Tier 1: Gemini rotated keys (shuffled -- equal peers, load balancing).
    Tier 2: PROVIDER_<N>_* groups in numeric order, models as written
            (configured order = priority).
    Tier 3: legacy FALLBACK_* endpoint (shuffled).
    Tier 4: Cloudflare AI (shuffled).
    Only tiers that actually have credentials are queued.
    """
    execution_queue = []

    # --- TIER 1: GEMINI (N API KEYS - SHUFFLED FOR LOAD BALANCING) ---
    gemini_temperature = float(os.getenv("GEMINI_TEMPERATURE", "1.0"))
    primary_model = os.getenv("DEFAULT_MODEL", "gemini/gemini-3.5-flash")
    gemini_keys = _scan_gemini_keys()
    if gemini_keys and primary_model:
        gemini_models = []
        for i, api_key in enumerate(gemini_keys):
            gemini_models.append(
                {
                    "model": primary_model,
                    "api_key": api_key,
                    "base_url": None,
                    "provider": f"Gemini Key {i+1} (Primary)",
                    "temperature": gemini_temperature,
                }
            )
        random.shuffle(gemini_models)
        execution_queue.extend(gemini_models)

    # --- TIER 2: FIRST-CLASS PROVIDERS (CONFIGURED ORDER = PRIORITY) ---
    for entry in _scan_provider_entries():
        for model_name in entry["models"]:
            execution_queue.append(
                {
                    "model": model_name,
                    "api_key": entry["api_key"],
                    "base_url": entry["base_url"],
                    "provider": entry["label"],
                    "temperature": entry["temperature"],
                }
            )

    # --- TIER 3: LEGACY FALLBACK (SHUFFLED) ---
    fallback_api_key = os.getenv("FALLBACK_API_KEY")
    fallback_base_url = os.getenv(
        "FALLBACK_BASE_URL", "https://openrouter.ai/api/v1"
    )
    fallback_models = [
        m.strip() for m in os.getenv("FALLBACK_MODELS", "").split(",") if m.strip()
    ]
    fallback_temperature = float(os.getenv("FALLBACK_TEMPERATURE", "0.4"))
    if fallback_api_key:
        tier3_models = []
        for fb_model in fallback_models:
            safe_model_name = fb_model
            if not any(
                fb_model.startswith(prefix)
                for prefix in [
                    "openai/",
                    "anthropic/",
                    "openrouter/",
                    "groq/",
                    "gemini/",
                ]
            ):
                safe_model_name = f"openai/{fb_model}"

            tier3_models.append(
                {
                    "model": safe_model_name,
                    "api_key": fallback_api_key,
                    "base_url": fallback_base_url,
                    "provider": "OpenRouter (Fallback 1)",
                    "temperature": fallback_temperature,
                }
            )
        random.shuffle(tier3_models)
        execution_queue.extend(tier3_models)

    # --- TIER 4: CLOUDFLARE (SHUFFLED) ---
    cf_account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    cf_api_token = os.getenv("CLOUDFLARE_API_TOKEN")
    cf_models = [
        m.strip() for m in os.getenv("CLOUDFLARE_MODELS", "").split(",") if m.strip()
    ]
    cf_temperature = float(os.getenv("CLOUDFLARE_TEMPERATURE", "0.4"))
    if cf_account_id and cf_api_token:
        cf_base_url = (
            f"https://api.cloudflare.com/client/v4/accounts/{cf_account_id}/ai/v1"
        )
        tier4_models = []
        for cf_model in cf_models:
            tier4_models.append(
                {
                    "model": f"cloudflare/{cf_model}",
                    "api_key": cf_api_token,
                    "base_url": cf_base_url,
                    "provider": "Cloudflare (Fallback 2)",
                    "temperature": cf_temperature,
                }
            )
        random.shuffle(tier4_models)
        execution_queue.extend(tier4_models)

    if not execution_queue:
        raise Exception(
            "[LLM_ROUTER] No models configured. Check your .env."
        )
    return execution_queue


# ==========================================
# 5b. SUB-AGENT MODEL PROFILES (UPDATE-018)
# ==========================================
# orion_config.json can pin a model per sub-agent role:
#   "subagent_models": {"explorer"|"verifier"|"workers":
#       {"group": "gemini"|"provider_<N>"|"fallback", "model": "<name>"} | null}
# A profile is a SINGLE-TARGET queue built from the credential group that
# ALREADY exists in the environment — no new secrets are ever introduced.
# A malformed profile, an unknown group or a group without credentials
# degrades to the main router with a logged warning (never to a crash):
# the sub-run simply proceeds on the normal tiers.
_provider_group_pattern = re.compile(r"^provider_([1-9][0-9]?)$")

# Same prefix rules as build_execution_queue, per group.
_FALLBACK_PREFIXES = ("openai/", "anthropic/", "openrouter/", "groq/", "gemini/")


def build_profile_queue(profile) -> list | None:
    """One-target execution queue for a sub-agent profile, or None.

    None means "profile unusable" (malformed shape, unknown group, or the
    group has no credentials in this environment); the caller then falls
    back to build_execution_queue(). Gemini profiles rotate the group's
    existing N keys (peers, like tier 1); provider/fallback profiles are a
    single entry with that group's key, base URL and temperature.
    """
    if not isinstance(profile, dict):
        return None
    group = str(profile.get("group") or "").strip().lower()
    model = str(profile.get("model") or "").strip()
    if not group or not model:
        return None

    if group == "gemini":
        keys = _scan_gemini_keys()
        if not keys:
            return None
        gemini_temperature = float(os.getenv("GEMINI_TEMPERATURE", "1.0"))
        model_name = model if "/" in model else f"gemini/{model}"
        queue = [
            {
                "model": model_name,
                "api_key": key,
                "base_url": None,
                "provider": f"Gemini Key {i + 1} (profile)",
                "temperature": gemini_temperature,
            }
            for i, key in enumerate(keys)
        ]
        random.shuffle(queue)
        return queue

    match = _provider_group_pattern.match(group)
    if match:
        wanted = int(match.group(1))
        for entry in _scan_provider_entries():
            if entry["index"] != wanted:
                continue
            model_name = model if "/" in model else f"openai/{model}"
            return [
                {
                    "model": model_name,
                    "api_key": entry["api_key"],
                    "base_url": entry["base_url"],
                    "provider": f"{entry['label']} (profile)",
                    "temperature": entry["temperature"],
                }
            ]
        return None

    if group == "fallback":
        api_key = os.getenv("FALLBACK_API_KEY")
        if not api_key:
            return None
        model_name = (
            model
            if model.startswith(_FALLBACK_PREFIXES)
            else f"openai/{model}"
        )
        return [
            {
                "model": model_name,
                "api_key": api_key,
                "base_url": os.getenv(
                    "FALLBACK_BASE_URL", "https://openrouter.ai/api/v1"
                ),
                "provider": "OpenRouter (Fallback 1, profile)",
                "temperature": float(os.getenv("FALLBACK_TEMPERATURE", "0.4")),
            }
        ]

    return None


def model_options() -> list:
    """Credential groups + their models for the setup wizard (env-derived).

    GET /models/options serves this verbatim: group ids match the profile
    'group' field, labels are log-friendly, models are the configured
    names. NEVER includes key values (security posture §6: env privacy —
    only key NAMES/counts may surface, exactly like /mcp/status).
    """
    options = []
    primary_model = os.getenv("DEFAULT_MODEL", "gemini/gemini-3.5-flash")
    gemini_keys = _scan_gemini_keys()
    if gemini_keys and primary_model:
        options.append(
            {
                "group": "gemini",
                "label": f"Gemini ({len(gemini_keys)} key(s), Primary)",
                "models": [primary_model],
            }
        )
    for entry in _scan_provider_entries():
        options.append(
            {
                "group": f"provider_{entry['index']}",
                "label": entry["label"],
                "models": list(entry["models"]),
            }
        )
    if os.getenv("FALLBACK_API_KEY"):
        fallback_models = [
            m.strip()
            for m in os.getenv("FALLBACK_MODELS", "").split(",")
            if m.strip()
        ]
        options.append(
            {
                "group": "fallback",
                "label": "OpenRouter (Fallback 1)",
                "models": fallback_models,
            }
        )
    return options


async def get_ai_response(messages: list, tools: list = None, profile: dict = None) -> dict:
    """
    Hybrid Router: Gemini (N-key rotation) -> PROVIDER_<N>_* groups ->
    legacy FALLBACK_* -> Cloudflare. See build_execution_queue().

    UPDATE-018: optional ``profile`` ({"group", "model"}, from
    orion_config subagent_models) pins the call to ONE target built from
    the group's existing credentials. A broken profile (break/absent shape,
    unknown group, group without credentials) NEVER fails the call: it logs
    a warning and falls back to the full main router.
    """
    execution_queue = None
    if profile:
        execution_queue = build_profile_queue(profile)
        if execution_queue:
            print(
                f"[LLM_ROUTER] Sub-agent profile active: group={profile.get('group')} "
                f"model={execution_queue[0]['model']}"
            )
        else:
            print(
                f"[LLM_ROUTER] [WARNING] Sub-agent profile group={profile.get('group')!r} "
                f"model={profile.get('model')!r} is invalid or its group has no "
                "credentials in this environment; falling back to the main router."
            )
    if not execution_queue:
        execution_queue = build_execution_queue()

    last_error = None

    for target in execution_queue:
        model_name = target["model"]
        provider_name = target["provider"]
        # >>> FIX: use this tier's own temperature, not 1.0 for everything.
        target_temperature = target.get("temperature", 0.4)

        # Configure LiteLLM dynamically
        if "gemini" in model_name.lower():
            os.environ["GEMINI_API_KEY"] = target["api_key"]
            litellm.api_key = target["api_key"]
            litellm.api_base = None
        else:
            litellm.api_key = target["api_key"]
            litellm.api_base = target["base_url"]

        print("[LLM_ROUTER] ========================================")
        print(f"[LLM_ROUTER] >> Trying: {model_name}")
        print(f"[LLM_ROUTER] >> Provider: {provider_name}")
        print(f"[LLM_ROUTER] >> Temperature: {target_temperature}")
        print("[LLM_ROUTER] ========================================")

        for attempt in range(MAX_RETRIES):
            try:
                # >>> Temperature 1.0 is MANDATORY specifically for Gemini 3.x to
                # avoid hang/timeout. Fallback models (OpenRouter/Cloudflare) use a
                # lower temperature (default 0.4) because those models are generally
                # smaller and easily produce incoherent output when the temperature
                # is forced high. <<<
                # >>> PATCH ORION-6 (CRITICAL) ==============================
                # `reasoning_effort` is the only way to send `thinking_level` to
                # Gemini 3+ through litellm. From the litellm documentation:
                #   "If you omit reasoning_effort, LiteLLM does NOT send a default
                #    thinking_level, so the request uses the Gemini API defaults."
                # And the API default for gemini-3.5-flash-lite is MINIMAL, which
                # per Google's documentation causes
                #   "premature tool termination on multi-step tasks"
                # -- exactly the "only does the first task" complaint.
                #
                # Only sent for Gemini models; other providers may reject it.
                # =================================================================
                extra_kwargs = {}
                if "gemini" in model_name.lower():
                    extra_kwargs["reasoning_effort"] = REASONING_EFFORT
                    # gemini-3.5-flash-lite IGNORES custom temperature values,
                    # so it is not sent unless explicitly requested via .env
                    if not KIRIM_TEMPERATURE_GEMINI:
                        target_temperature = None

                call_kwargs = {
                    "model": model_name,
                    "messages": messages,
                    "tools": tools,
                    "timeout": TIMEOUT_SEC,
                    "max_tokens": MAX_OUTPUT_TOKENS,
                    **extra_kwargs,
                }
                if target_temperature is not None:
                    call_kwargs["temperature"] = target_temperature

                response = await litellm.acompletion(**call_kwargs)

                print(
                    f"[LLM_ROUTER] [SUCCESS] Succeeded with: {model_name} ({provider_name})"
                )
                return response

            except RateLimitError as e:
                print(
                    f"[LLM_ROUTER] [WARNING] {model_name} RATE LIMIT detected! Trying another key/model..."
                )
                last_error = e
                break

            except BadRequestError as e:
                err_str = str(e).lower()
                if (
                    "failed to call a function" in err_str
                    or "invalid schema" in err_str
                ):
                    print(
                        f"[LLM_ROUTER] [CRITICAL] {model_name} rejected the tools schema. Stopping attempts."
                    )
                    last_error = e
                    break
                else:
                    print(
                        f"[LLM_ROUTER] [WARNING] {model_name} rejected request (400): {str(e)[:100]}"
                    )
                    last_error = e
                    break

            except (Timeout, APIConnectionError) as e:
                last_error = e
                # >>> FIX: this used to always `break` even when MAX_RETRIES > 1,
                # so per-model retry never actually happened.
                # Transient errors (timeout/connection) deserve a retry on the
                # SAME MODEL first before giving up and switching providers.
                if attempt < MAX_RETRIES - 1:
                    # >>> PATCH ORION-7: this used to `continue` immediately (instant retry).
                    # For timeout/429, firing again instantly almost certainly fails again
                    # and just burns free-tier quota. Now there is a delay + jitter.
                    _jeda = min(BACKOFF_BASE ** (attempt + 1), BACKOFF_CAP) * random.uniform(0.7, 1.3)
                    print(
                        f"[LLM_ROUTER] [WARNING] {model_name} TIMEOUT/CONNECTION FAILED! "
                        f"Retry attempt {attempt + 2}/{MAX_RETRIES} in {_jeda:.1f} s..."
                    )
                    await asyncio.sleep(_jeda)
                    continue
                print(
                    f"[LLM_ROUTER] [WARNING] {model_name} TIMEOUT/CONNECTION FAILED after {MAX_RETRIES}x attempts. Moving to the next model."
                )
                break

            except Exception as e:
                # ADDED: print the raw error from LiteLLM
                print(f"[LLM_ROUTER] [DEBUG] FULL ERROR: {e!r}")
                err_str = str(e).lower()
                if (
                    "connection" in err_str
                    or "timeout" in err_str
                    or "disconnect" in err_str
                    or "incomplete" in err_str
                ):
                    last_error = e
                    if attempt < MAX_RETRIES - 1:
                        print(
                            f"[LLM_ROUTER] [WARNING] {model_name} unstable connection. "
                            f"Retry attempt {attempt + 2}/{MAX_RETRIES} on the same model..."
                        )
                        continue
                    print(
                        f"[LLM_ROUTER] [WARNING] {model_name} unstable connection after {MAX_RETRIES}x attempts. Moving to the next model."
                    )
                    break
                else:
                    print(
                        f"[LLM_ROUTER] [WARNING] {model_name} error: {str(e)[:100]}. Trying the next one..."
                    )
                    last_error = e
                    break

    print("[LLM_ROUTER] [CRITICAL] All AI models failed to respond.")
    raise Exception(
        f"All AI models are busy/failing. Last error: {last_error!s}"
    )
