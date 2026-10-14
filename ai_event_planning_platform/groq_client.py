import os

import httpx
import streamlit as st
from groq import Groq
from streamlit.errors import StreamlitSecretNotFoundError


GROQ_BASE_URL = "https://api.groq.com"
GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_MAX_COMPLETION_TOKENS = 1200


class GroqConfigurationError(RuntimeError):
    """Raised when the Groq API key is not configured."""


def load_groq_api_key() -> str:
    try:
        api_key = st.secrets.get("GROQ_API_KEY")
    except StreamlitSecretNotFoundError:
        api_key = None
    if not isinstance(api_key, str) or not api_key.strip():
        api_key = os.environ.get("GROQ_API_KEY")
    if not isinstance(api_key, str) or not api_key.strip():
        raise GroqConfigurationError(
            "Groq is not configured. Add GROQ_API_KEY to Streamlit secrets or the environment."
        )
    return api_key.strip()


def create_groq_client() -> Groq:
    return Groq(
        api_key=load_groq_api_key(),
        base_url=GROQ_BASE_URL,
        timeout=httpx.Timeout(
            30.0,
            connect=5.0,
            read=25.0,
            write=10.0,
            pool=5.0,
        ),
        max_retries=0,
    )