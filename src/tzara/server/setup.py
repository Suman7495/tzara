"""What each model needs before it can be used, and the means to get it ready."""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import httpx

from tzara.agent.models import ModelCatalog

logger = logging.getLogger(__name__)


class KeyStore:
    """Keeps the API keys of the cloud providers in a file only this user can read.

    The server runs under launchd, which has none of the user's shell variables, so a key
    typed in Settings is saved here and put in the environment, where the providers' libraries
    look for it. A key set in the environment already is used as it is.
    """

    VARIABLES: ClassVar[dict[str, str]] = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "google_genai": "GOOGLE_API_KEY",
    }
    FILE_MODE = 0o600
    MAX_KEY_CHARS = 500

    def __init__(self, path: Path) -> None:
        """Create a store.

        Args:
            path: File to keep the keys in.
        """
        self._path = path

    def apply(self) -> None:
        """Put every saved key in the environment, unless the environment has one already."""
        for provider, key in self._read().items():
            variable = self.VARIABLES.get(provider)
            if variable and key:
                os.environ.setdefault(variable, key)

    def has_key(self, provider: str) -> bool:
        """Whether a key for ``provider`` is saved or in the environment."""
        variable = self.VARIABLES.get(provider)
        return bool(variable and (os.environ.get(variable) or self._read().get(provider)))

    def save(self, provider: str, key: str) -> None:
        """Save a key and use it from now on.

        Raises:
            ValueError: If the provider is unknown or the key is empty, has spaces or is too long.
        """
        variable = self.VARIABLES.get(provider)
        key = key.strip()
        if variable is None:
            raise ValueError(f"{provider} does not use a key")
        if not key or len(key) > self.MAX_KEY_CHARS or any(c.isspace() for c in key):
            raise ValueError("that does not look like an API key")
        keys = self._read()
        keys[provider] = key
        self._write(keys)
        os.environ[variable] = key

    def _read(self) -> dict[str, str]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): v for k, v in data.items() if isinstance(v, str)}

    def _write(self, keys: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, self.FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(keys, handle)
        temporary.replace(self._path)


@dataclass
class Download:
    """How far one model's download has got.

    Attributes:
        model: The model's spec, ``ollama:name``.
        fraction: 0 to 1, or ``None`` while Ollama has not said yet.
        error: What went wrong, if it failed.
        finished: Whether the download is over.
    """

    model: str
    fraction: float | None = None
    error: str | None = None
    finished: bool = False


class ModelSetup:
    """Tells which models are ready to use and downloads the local ones that are not."""

    def __init__(
        self,
        keys: KeyStore,
        ollama_url: str,
        client: httpx.Client | None = None,
    ) -> None:
        """Create the helper.

        Args:
            keys: Where the cloud providers' keys are kept.
            ollama_url: Address of the local Ollama server.
            client: HTTP client for Ollama. Replaced in tests.
        """
        self._keys = keys
        self._url = ollama_url.rstrip("/")
        self._client = client or httpx.Client(timeout=httpx.Timeout(5.0, read=None))
        self._downloads: dict[str, Download] = {}
        self._lock = threading.Lock()

    @property
    def keys(self) -> KeyStore:
        """The store of cloud providers' keys."""
        return self._keys

    def installed(self) -> set[str] | None:
        """The names of the models Ollama has, or ``None`` if Ollama does not answer."""
        try:
            response = self._client.get(f"{self._url}/api/tags", timeout=3.0)
            models = response.json().get("models", [])
        except (httpx.HTTPError, ValueError, AttributeError):
            return None
        names = {str(item.get("name")) for item in models if isinstance(item, dict)}
        return names | {name.removesuffix(":latest") for name in names}

    def overview(self) -> list[dict[str, object]]:
        """Every provider with its models, and what each still needs.

        ``need`` is ``ready``, ``download`` (a local model Ollama does not have),
        ``ollama`` (Ollama itself is not running) or ``key`` (a cloud provider with no key).
        """
        installed = self.installed()
        providers: list[dict[str, object]] = []
        for provider, models in ModelCatalog.MODELS.items():
            local = provider == ModelCatalog.OLLAMA
            entries = []
            for model_id, description in models.items():
                if local:
                    name = model_id.split(":", 1)[1]
                    need = (
                        "ollama"
                        if installed is None
                        else "ready"
                        if name in installed
                        else "download"
                    )
                else:
                    need = "ready" if self._keys.has_key(provider) else "key"
                entries.append({"id": model_id, "description": description, "need": need})
            providers.append({"name": provider, "models": entries})
        return providers

    def start_download(self, model: str) -> Download:
        """Download a local model in the background. Asking again while it runs changes nothing.

        Raises:
            ValueError: If ``model`` is not an Ollama model.
        """
        if ModelCatalog.provider_of(model) != ModelCatalog.OLLAMA:
            raise ValueError("only local (Ollama) models are downloaded")
        with self._lock:
            current = self._downloads.get(model)
            if current is not None and not current.finished:
                return current
            download = Download(model)
            self._downloads[model] = download
        threading.Thread(
            target=self._pull, args=(download,), name="tzara-model-download", daemon=True
        ).start()
        return download

    def downloads(self) -> list[Download]:
        """The downloads started since the server began, newest state."""
        with self._lock:
            return list(self._downloads.values())

    def _pull(self, download: Download) -> None:
        name = download.model.split(":", 1)[1]
        try:
            with self._client.stream(
                "POST", f"{self._url}/api/pull", json={"model": name, "stream": True}
            ) as response:
                if response.status_code != httpx.codes.OK:
                    raise RuntimeError(f"Ollama refused (HTTP {response.status_code})")
                for line in response.iter_lines():
                    if not line.strip():
                        continue
                    status = json.loads(line)
                    if "error" in status:
                        raise RuntimeError(str(status["error"]))
                    if status.get("total"):
                        download.fraction = status.get("completed", 0) / status["total"]
        except (httpx.HTTPError, RuntimeError, ValueError) as error:
            logger.warning("download of %s failed: %s", name, error)
            download.error = (
                "Ollama is not running. Open the Ollama app, then try again."
                if isinstance(error, httpx.ConnectError)
                else f"Could not download {name}: {error}"
            )
        else:
            download.fraction = 1.0
        download.finished = True
