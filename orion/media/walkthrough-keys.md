# Keys and models

All configuration lives in one file: **`backend/.env`** (never committed,
never leaves your machine). After editing it, restart the backend.

## Free Gemini keys

Add as many as you have — Orion rotates them per request and jumps to the
next key on rate limits:

```ini
GEMINI_API_KEY_1=...
GEMINI_API_KEY_2=...
GEMINI_API_KEY_3=...
```

Get free keys at <https://aistudio.google.com/apikey>.

## Smarter models (OpenAI, Anthropic, DeepSeek, Qwen, …)

Orion routes through [LiteLLM](https://docs.litellm.ai/), so any provider
it supports works. The easiest path is one OpenRouter key, which unlocks
hundreds of models as a fallback tier:

```ini
FALLBACK_API_KEY=sk-or-...
FALLBACK_MODELS=openai/gpt-5, anthropic/claude-sonnet-4-5, deepseek/deepseek-reasoner
```

Direct endpoints (OpenAI, DeepSeek, Qwen/DashScope, Groq, …) work too —
see the **LLM** section of the
[setup guide](https://github.com/grenitho/orion/blob/main/docs/PANDUAN_SETUP.md).
