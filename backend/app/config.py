import os
from dataclasses import dataclass

from sqlalchemy import URL


@dataclass(frozen=True)
class Settings:
    database_url: str | URL
    origins: tuple[str, ...]
    cookie_secure: bool = False
    session_hours: int = 12
    model_base_url: str = ''
    model_api_key: str = ''
    model_name: str = ''
    model_response_mode: str = 'json_schema'
    model_timeout_seconds: float = 60.0
    model_max_output_tokens: int = 4000
    analysis_worker_enabled: bool = False

    @property
    def model_configured(self):
        return bool(self.model_base_url and self.model_api_key and self.model_name)

    @classmethod
    def from_env(cls):
        url = os.environ.get('DATABASE_URL') or URL.create(
            'postgresql+psycopg',
            username=os.environ.get('POSTGRES_USER', 'resume_app'),
            password=os.environ.get('POSTGRES_PASSWORD', ''),
            host=os.environ.get('DB_HOST', 'db'),
            port=int(os.environ.get('DB_PORT', '5432')),
            database=os.environ.get('POSTGRES_DB', 'resume_match'),
        )
        secure = os.environ.get('COOKIE_SECURE', 'false').lower()
        if secure not in ('true', 'false'):
            raise ValueError('COOKIE_SECURE must be true or false')
        hours = int(os.environ.get('SESSION_HOURS', '12'))
        if not 1 <= hours <= 168:
            raise ValueError('SESSION_HOURS must be between 1 and 168')
        origins = tuple(x.strip().rstrip('/') for x in os.environ.get(
            'APP_ORIGINS',
            'http://localhost:5173,http://localhost:8080,http://127.0.0.1:8080',
        ).split(',') if x.strip())
        if not origins:
            raise ValueError('APP_ORIGINS cannot be empty')
        response_mode = os.environ.get('MODEL_RESPONSE_MODE', 'json_schema')
        if response_mode not in ('json_schema', 'json_object'):
            raise ValueError('MODEL_RESPONSE_MODE must be json_schema or json_object')
        timeout = float(os.environ.get('MODEL_TIMEOUT_SECONDS', '60'))
        if not 1 <= timeout <= 180:
            raise ValueError('MODEL_TIMEOUT_SECONDS must be between 1 and 180')
        max_tokens = int(os.environ.get('MODEL_MAX_OUTPUT_TOKENS', '4000'))
        if not 256 <= max_tokens <= 32000:
            raise ValueError('MODEL_MAX_OUTPUT_TOKENS must be between 256 and 32000')
        worker = os.environ.get('ANALYSIS_WORKER_ENABLED', 'true').lower()
        if worker not in ('true', 'false'):
            raise ValueError('ANALYSIS_WORKER_ENABLED must be true or false')
        return cls(
            url, origins, secure == 'true', hours,
            os.environ.get('MODEL_BASE_URL', '').rstrip('/'),
            os.environ.get('MODEL_API_KEY', ''),
            os.environ.get('MODEL_NAME', ''),
            response_mode, timeout, max_tokens, worker == 'true',
        )
