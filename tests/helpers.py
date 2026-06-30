import tempfile
from pathlib import Path
from copy import deepcopy

import config
from starlette.requests import Request

from src.api_key_store import api_key_store
from src.password_hashing import create_password_hash
from src.session_store import session_store


class ConfigIsolationMixin:
    def setUp(self):
        super().setUp()
        self._config_json_dir = tempfile.TemporaryDirectory()
        self._original_config_json_path = config._CONFIG_JSON_PATH
        config._CONFIG_JSON_PATH = str(Path(self._config_json_dir.name) / "config.json")
        self._original_config = config._config_cache.copy()
        self._original_user_settings = deepcopy(config._user_settings_cache)
        reset_runtime_stores()

    def tearDown(self):
        reset_runtime_stores()
        config._user_settings_cache = deepcopy(self._original_user_settings)
        config._config_cache = self._original_config.copy()
        config._CONFIG_JSON_PATH = self._original_config_json_path
        self._config_json_dir.cleanup()
        super().tearDown()


class TempConfigMixin(ConfigIsolationMixin):
    def setUp(self):
        super().setUp()
        self._temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._temp_dir.cleanup()
        super().tearDown()

    @property
    def temp_path(self) -> Path:
        return Path(self._temp_dir.name)


def reset_runtime_stores():
    api_key_store._keys = []
    api_key_store._loaded_path = None
    api_key_store._loaded_mtime = None
    session_store.sessions.clear()


def write_users_file(directory: Path, users=None) -> Path:
    users = users or {"admin": "secret-password"}
    users_file = directory / "users.txt"
    lines = [
        f"{username}:{create_password_hash(password)}\n"
        for username, password in users.items()
    ]
    users_file.write_text("".join(lines), encoding="utf-8")
    return users_file


def configure_users_file(directory: Path, users=None) -> Path:
    users_file = write_users_file(directory, users)
    config._config_cache["CODEBUDDY_USERS_FILE"] = str(users_file)
    return users_file


def make_request(
    authorization: str = "",
    cookie: str = "",
    scheme: str = "http",
    method: str = "GET",
    path: str = "/",
    extra_headers=None,
) -> Request:
    headers = []
    if authorization:
        headers.append((b"authorization", authorization.encode("utf-8")))
    if cookie:
        headers.append((b"cookie", cookie.encode("utf-8")))
    for key, value in (extra_headers or {}).items():
        headers.append((key.lower().encode("latin-1"), str(value).encode("latin-1")))
    return Request({
        "type": "http",
        "method": method,
        "path": path,
        "scheme": scheme,
        "headers": headers,
    })


class FakeStreamResponse:
    def __init__(self, chunks, status_code=200, text=""):
        self.status_code = status_code
        self.text = text
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_text(self, chunk_size=None):
        for chunk in self._chunks:
            yield chunk

    async def aread(self):
        return self.text.encode("utf-8")


class FakeHttpClient:
    def __init__(self, chunks, status_code=200, text=""):
        self._chunks = chunks
        self.status_code = status_code
        self.text = text
        self.requests = []

    def stream(self, method, url, json=None, headers=None):
        self.requests.append({"method": method, "url": url, "json": json, "headers": headers})
        return FakeStreamResponse(self._chunks, self.status_code, self.text)

    async def post(self, url, json=None, headers=None):
        self.requests.append({"method": "POST", "url": url, "json": json, "headers": headers})
        return FakeStreamResponse(self._chunks, self.status_code, self.text)


async def async_chunks(*chunks):
    for chunk in chunks:
        yield chunk
