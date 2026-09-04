
"""
Provider-agnostic LLM client.

Supported providers:
  LLM_PROVIDER=gemini
  LLM_PROVIDER=openrouter
  LLM_PROVIDER=anthropic
  LLM_PROVIDER=grok
  LLM_PROVIDER=xai

The Explainer agents call only call_llm(), so provider-specific
code stays isolated in this file.
"""

import os
import re


def _selected_provider() -> str:
    return (os.environ.get("LLM_PROVIDER", "openrouter") or "openrouter").lower()


def call_llm(
    system_prompt: str,
    user_message: str,
    max_tokens: int = 300
) -> str:

    provider = _selected_provider()

    if provider == "gemini":
        return _call_gemini(system_prompt, user_message, max_tokens)

    elif provider in {"openrouter", "open_router"}:
        return _call_openrouter(system_prompt, user_message, max_tokens)

    elif provider == "anthropic":
        return _call_anthropic(system_prompt, user_message, max_tokens)

    elif provider in {"grok", "xai"}:
        return _call_grok(system_prompt, user_message, max_tokens)

    else:
        raise ValueError(
            f"Unknown LLM_PROVIDER '{provider}'. "
            "Use gemini, openrouter, anthropic, grok, or xai."
        )


# ============================================================
# GEMINI
# ============================================================

def _call_gemini(
    system_prompt: str,
    user_message: str,
    max_tokens: int
) -> str:

    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY not set. "
            "Get a free key at https://aistudio.google.com/apikey"
        )

    client = genai.Client(api_key=api_key)

    model_name = os.environ.get(
        "GEMINI_MODEL",
        "gemini-2.5-flash"
    )

    response = client.models.generate_content(
        model=model_name,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_tokens,
            temperature=0.0,
            response_mime_type="application/json",
        ),
    )

    if not response.text:
        raise RuntimeError(
            "Gemini returned an empty response."
        )

    return _clean_json_response(response.text)


# ============================================================
# CLEAN GEMINI JSON
# ============================================================

def _clean_json_response(text: str) -> str:
    """
    Makes Gemini output safe for json.loads().

    Handles:
      - Markdown code fences
      - 'Here is the JSON:'
      - Extra whitespace
      - JSON surrounded by other text
    """

    text = text.strip()

    # Remove markdown code fences
    text = re.sub(r"```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```\s*", "", text)

    text = text.strip()

    # If Gemini added text before/after the JSON,
    # extract the outermost JSON object.
    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]

    return text.strip()


# ============================================================
# OPENROUTER
# ============================================================

def _call_openrouter(
    system_prompt: str,
    user_message: str,
    max_tokens: int
) -> str:

    from openai import OpenAI

    api_key = os.environ.get("OPENROUTER_API_KEY")

    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY not set. "
            "Get a key at https://openrouter.ai/keys"
        )

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key
    )

    model_name = os.environ.get(
        "OPENROUTER_MODEL",
        "openai/gpt-4o-mini-2024-07-18"
    )

    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": user_message
            },
        ],
        max_tokens=max_tokens,
        temperature=0.0,
    )

    content = response.choices[0].message.content

    if not content:
        raise RuntimeError(
            "OpenRouter returned an empty response."
        )

    return _clean_json_response(content)


# ============================================================
# ANTHROPIC
# ============================================================

def _call_anthropic(
    system_prompt: str,
    user_message: str,
    max_tokens: int
) -> str:

    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")

    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set."
        )

    client = anthropic.Anthropic(
        api_key=api_key
    )

    model_name = os.environ.get(
        "ANTHROPIC_MODEL",
        "claude-sonnet-4-5"
    )

    response = client.messages.create(
        model=model_name,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": user_message
            }
        ],
    )

    if not response.content:
        raise RuntimeError(
            "Anthropic returned an empty response."
        )

    return response.content[0].text.strip()


# ============================================================
# GROK / XAI
# ============================================================

def _call_grok(
    system_prompt: str,
    user_message: str,
    max_tokens: int
) -> str:

    from openai import OpenAI

    api_key = os.environ.get("GROK_API_KEY") or os.environ.get("XAI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "GROK_API_KEY not set. Set GROK_API_KEY (or XAI_API_KEY) in the environment "
            "before using the grok/xAI provider."
        )

    client = OpenAI(
        base_url="https://api.x.ai/v1",
        api_key=api_key,
    )

    model_name = os.environ.get(
        "GROK_MODEL",
        "grok-3-mini-fast-beta"
    )

    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        max_tokens=max_tokens,
        temperature=0.0,
    )

    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("Grok returned an empty response.")

    return _clean_json_response(content)

