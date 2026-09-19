import os
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# Load local environment variables
load_dotenv()

# Azure Key Vault configuration — same pattern as ai-callcenter (only used when IS_LOCAL=false).
KEY_VAULT_NAME = os.getenv("AZURE_KEY_VAULT_NAME")
KV_URI = f"https://{KEY_VAULT_NAME}.vault.azure.net" if KEY_VAULT_NAME else None
_kv_client = None


def get_kv_client():
    """Get or create the Key Vault client (lazy; azure packages are optional locally)."""
    global _kv_client
    if _kv_client is None and KV_URI:
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient

            _kv_client = SecretClient(vault_url=KV_URI, credential=DefaultAzureCredential())
            print(f"Successfully connected to Key Vault: {KV_URI}")
        except Exception as e:
            print(f"Failed to connect to Key Vault: {e}")
            return None
    return _kv_client


def get_secret(secret_name: str, default: Optional[str] = None) -> Optional[str]:
    """Retrieve a secret from Azure Key Vault if available."""
    client = get_kv_client()
    if not client:
        return default
    try:
        return client.get_secret(secret_name).value
    except Exception as e:
        print(f"Error retrieving secret '{secret_name}': {e}")
        return default


# Local dev is the default for this project (ai-callcenter defaults to Key Vault).
IS_LOCAL = os.getenv("IS_LOCAL", "true").lower() == "true"


def _cfg(name: str, default: Optional[str] = None) -> Optional[str]:
    """ai-callcenter's convention: `os.getenv(X) if IS_LOCAL else get_secret(Y)` where
    Y = X with underscores stripped and upper-cased (OPENAI_API_KEY -> OPENAIAPIKEY)."""
    if IS_LOCAL:
        value = os.getenv(name)
    else:
        value = get_secret(name.replace("_", "").upper())
    return value if value not in (None, "") else default


def _bool(name: str, default: str = "false") -> bool:
    return str(_cfg(name, default)).lower() == "true"


class Settings(BaseSettings):
    IS_LOCAL: bool = IS_LOCAL
    app_name: str = "AI VOICE-FIRST EMERGENCY AGENT"
    app_version: str = "V1"
    debug: bool = os.getenv("debug", "False").lower() == "true"

    # ── Database ──
    DATABASE_URL: Optional[str] = _cfg("DATABASE_URL")
    PGHOST: Optional[str] = _cfg("PGHOST")
    PGPORT: Optional[str] = _cfg("PGPORT", "5432")
    PGUSER: Optional[str] = _cfg("PGUSER")
    PGPASSWORD: Optional[str] = _cfg("PGPASSWORD")
    PGDATABASE: Optional[str] = _cfg("PGDATABASE")

    # ── OpenAI ──
    OPENAI_API_KEY: Optional[str] = _cfg("OPENAI_API_KEY")
    OPENAI_REALTIME_MODEL: str = _cfg("OPENAI_REALTIME_MODEL", "gpt-realtime-1.5")
    VOICE_NAME: str = _cfg("VOICE_NAME", "marin")
    SUMMARY_MODEL: str = _cfg("SUMMARY_MODEL", "gpt-4o-mini")
    EMBEDDING_MODEL: str = _cfg("EMBEDDING_MODEL", "text-embedding-3-small")
    USE_EMBEDDINGS: bool = _bool("USE_EMBEDDINGS", "false")

    # ── Twilio ──
    TWILIO_ACCOUNT_SID: Optional[str] = _cfg("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN: Optional[str] = _cfg("TWILIO_AUTH_TOKEN")
    TWILIO_PHONE_NUMBER: Optional[str] = _cfg("TWILIO_PHONE_NUMBER")
    PUBLIC_HOST: Optional[str] = _cfg("PUBLIC_HOST")
    TWILIO_VALIDATE_SIGNATURE: bool = _bool("TWILIO_VALIDATE_SIGNATURE", "false")

    # ── Dispatch behaviour ──
    DISPATCH_MODE: str = _cfg("DISPATCH_MODE", "autonomous")  # autonomous | approval
    CRITICAL_SEVERITY: int = int(_cfg("CRITICAL_SEVERITY", "85"))
    SMS_ENABLED: bool = _bool("SMS_ENABLED", "false")
    DEPARTMENT_WEBHOOK_URL: Optional[str] = _cfg("DEPARTMENT_WEBHOOK_URL")
    RESPONSE_DELAY_BUFFER_MIN: int = int(_cfg("RESPONSE_DELAY_BUFFER_MIN", "3"))

    # ── Geo ──
    GEOCODER_PROVIDER: str = _cfg("GEOCODER_PROVIDER", "hybrid")  # gazetteer | nominatim | hybrid
    DEFAULT_CITY: str = _cfg("DEFAULT_CITY", "Ahmedabad")
    DEFAULT_COUNTRY_CODE: str = _cfg("DEFAULT_COUNTRY_CODE", "in")
    CITY_CENTER_LAT: float = float(_cfg("CITY_CENTER_LAT", "23.0225"))
    CITY_CENTER_LNG: float = float(_cfg("CITY_CENTER_LNG", "72.5714"))
    DUPLICATE_RADIUS_M: int = int(_cfg("DUPLICATE_RADIUS_M", "500"))
    DUPLICATE_WINDOW_MIN: int = int(_cfg("DUPLICATE_WINDOW_MIN", "60"))

    # ── External geo services ──
    # Nearest department centre (fire station / police station / hospital ...): overpass (OpenStreetMap, no key)
    # | google_places (needs GOOGLE_MAPS_API_KEY) | seed (offline, seeded facilities table only).
    FACILITY_PROVIDER: str = _cfg("FACILITY_PROVIDER", "overpass")
    # Road distance / ETA: osrm (public or self-hosted OSRM) | google (Routes API) | haversine (no external call).
    ROUTING_PROVIDER: str = _cfg("ROUTING_PROVIDER", "osrm")
    GOOGLE_MAPS_API_KEY: Optional[str] = _cfg("GOOGLE_MAPS_API_KEY")
    OVERPASS_URL: str = _cfg("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
    OSRM_URL: str = _cfg("OSRM_URL", "https://router.project-osrm.org")
    EXTERNAL_HTTP_TIMEOUT_S: float = float(_cfg("EXTERNAL_HTTP_TIMEOUT_S", "6"))
    FACILITY_CACHE_TTL_S: int = int(_cfg("FACILITY_CACHE_TTL_S", "86400"))
    # Twilio escalation ladder: every seeded contact rings THIS number instead of the synthetic ones, so a
    # team can test the whole transfer with their own phone. Leave empty for the synthetic demo numbers.
    ESCALATION_DEMO_NUMBER: Optional[str] = _cfg("ESCALATION_DEMO_NUMBER")

    # ── Demo helpers ──
    SIMULATE_FLEET: bool = _bool("SIMULATE_FLEET", "true")
    SIM_SPEED_MULTIPLIER: float = float(_cfg("SIM_SPEED_MULTIPLIER", "12"))
    DASHBOARD_API_KEY: Optional[str] = _cfg("DASHBOARD_API_KEY")
    # Scripted scenario replayer (POST /api/dev/demo/{a|b|c|d}) for rehearsing with no phone call. OFF by default.
    ENABLE_DEV_ENDPOINTS: bool = _bool("ENABLE_DEV_ENDPOINTS", "false")

    ALLOWED_ORIGINS: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:5173"]

    @property
    def database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        if self.PGHOST and self.PGUSER:
            return (
                f"postgresql+psycopg2://{self.PGUSER}:{self.PGPASSWORD}"
                f"@{self.PGHOST}:{self.PGPORT}/{self.PGDATABASE}"
            )
        return "sqlite:///./emergency.db"

    model_config = {"extra": "allow"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
