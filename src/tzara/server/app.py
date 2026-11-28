"""The FastAPI application: Tzara's agent over local HTTP."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import signal
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from types import FrameType
from typing import Any, ClassVar

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from tzara.agent.memory import ConversationMemory
from tzara.agent.models import ModelCatalog
from tzara.agent.screen import ScreenContext
from tzara.agent.writer import WriteRequest
from tzara.config import ServerSettings, TzaraConfig
from tzara.mcp import McpConfigFile, McpHub, McpPresets, McpServerConfig
from tzara.memory import PersonalMemory
from tzara.server.files import NameStore, ServerInfo, ServerInfoFile, ServerPaths, TokenStore
from tzara.server.service import (
    ChatService,
    DoneEvent,
    ServiceBusy,
    ServiceNotReady,
    ServiceState,
    StatusEvent,
    TokenEvent,
    ToolEvent,
    TurnEvent,
)
from tzara.server.setup import KeyStore, ModelSetup

logger = logging.getLogger(__name__)


class ScreenBody(BaseModel):
    """What the user was looking at, inside ``POST /v1/chat``. Every part is optional."""

    app: str = Field(default="", max_length=500)
    window: str = Field(default="", max_length=2000)
    address: str = Field(default="", max_length=4000)
    selection: str = Field(default="", max_length=200_000)
    bundle: str = Field(default="", max_length=300)
    focus: str = Field(default="", max_length=600)
    typing: str = Field(default="", max_length=100_000)
    visible: str = Field(default="", max_length=400_000)


class ChatRequest(BaseModel):
    """Body of ``POST /v1/chat``."""

    message: str
    context: ScreenBody | None = None
    # Where the user is (a city and country), from the Mac's location, if they allowed it.
    place: str = Field(default="", max_length=200)

    @field_validator("message")
    @classmethod
    def not_blank(cls, value: str) -> str:
        """Reject messages that are empty or only whitespace."""
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


class WriteBody(BaseModel):
    """Body of ``POST /v1/write``: the field and the screen, read when the write key was tapped."""

    app: str = Field(default="", max_length=500)
    bundle: str = Field(default="", max_length=300)
    window: str = Field(default="", max_length=2000)
    address: str = Field(default="", max_length=4000)
    field: str = Field(default="", max_length=200_000)
    selection: str = Field(default="", max_length=200_000)
    visible: str = Field(default="", max_length=400_000)
    instruction: str = Field(default="", max_length=10_000)


class ModelRequest(BaseModel):
    """Body of ``PUT /v1/model``."""

    model: str


class AppRequest(BaseModel):
    """Body of ``POST /v1/apps``: a ready-made app by ``preset``, or the parts of one."""

    preset: str | None = Field(default=None, max_length=100)
    name: str = Field(default="", max_length=100)
    command: str = Field(default="", max_length=1000)
    args: list[str] = Field(default_factory=list, max_length=100)
    env: dict[str, str] = Field(default_factory=dict, max_length=50)
    allow_actions: bool = False
    token: str = Field(default="", max_length=1000)


class KeyRequest(BaseModel):
    """Body of ``PUT /v1/keys/{provider}``."""

    key: str = Field(max_length=1000)


class DownloadRequest(BaseModel):
    """Body of ``POST /v1/models/download``."""

    model: str = Field(max_length=200)


class ToolsRequest(BaseModel):
    """Body of ``PUT /v1/apps/{name}/tools``: the tools the model may use."""

    tools: list[str] = Field(max_length=500)


class NameRequest(BaseModel):
    """Body of ``PUT /v1/name``."""

    name: str


class ShutdownAwareServer(uvicorn.Server):
    """A uvicorn server that stops writing a reply as soon as it is told to exit.

    uvicorn waits for open connections before shutting down, so a streaming reply
    would otherwise hold up ``tzara stop`` until the grace period ran out.
    """

    def __init__(self, config: uvicorn.Config, service: ChatService) -> None:
        """Create the server.

        Args:
            config: uvicorn configuration.
            service: The service whose reply should be cancelled on shutdown.
        """
        super().__init__(config)
        self._service = service

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        """Cancel the reply being written, then shut down and exit normally.

        This does not call uvicorn's own handler. That one records the signal so it can send it
        to the process again after a graceful shutdown, which ends the process *by signal*. A
        service manager such as launchd then takes a deliberate ``tzara stop`` for a crash and
        restarts the server. Handled here, a stop is a normal exit with status 0.
        """
        self._service.close()
        if self.should_exit and sig == signal.SIGINT:
            self.force_exit = True  # a second Ctrl+C stops waiting
        else:
            self.should_exit = True


class TzaraServer:
    """HTTP interface to the agent, for clients on the same machine.

    Three guards protect it, because any program or web page on this computer can
    reach a local port. Requests must come to a loopback ``Host`` (defeating DNS
    rebinding), must not carry a browser ``Origin`` header (defeating cross-site
    requests), and must present the secret token.

    Replies stream as server-sent events (``text/event-stream``): ``token``
    events with pieces of text, ``status`` events saying what the agent is doing while it
    works, ``tool`` events when a tool starts or finishes, then one ``done`` or ``error``
    event.
    """

    STREAM_HEADERS: ClassVar[dict[str, str]] = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    GRACEFUL_SHUTDOWN_SECONDS: ClassVar[int] = 5
    # A server-sent-events comment: clients ignore it, but it counts as data.
    KEEP_ALIVE: ClassVar[str] = ": keep-alive\n\n"

    def __init__(
        self,
        service: ChatService,
        token: str,
        settings: ServerSettings,
        info_file: ServerInfoFile | None = None,
        setup: ModelSetup | None = None,
    ) -> None:
        """Build the application.

        Args:
            service: The agent to expose.
            token: Secret clients must present as a bearer token.
            settings: Server settings.
            info_file: Where to record the running server for clients. ``None``
                records nothing.
            setup: Tells which models are ready and gets them ready. ``None`` lists every
                model as ready and refuses downloads and keys.
        """
        self._setup = setup
        self._service = service
        self._token = token
        self._settings = settings
        self._info_file = info_file
        self._allowed_hosts = {
            f"{name}:{settings.port}".lower() for name in (settings.HOST, "localhost", "[::1]")
        }
        self._app = FastAPI(
            title="Tzara",
            lifespan=self._lifespan,
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
        )
        self._app.middleware("http")(self._guard)
        self._register_routes()

    @classmethod
    def serve(cls, config: TzaraConfig) -> None:
        """Run the server in this process until it is stopped.

        Args:
            config: Configuration.
        """
        paths = ServerPaths.from_settings(config.server)
        token = TokenStore(paths.token_file).load_or_create()
        names = NameStore(paths.name_file)
        saved_name = names.load()
        if saved_name and len(saved_name) <= config.server.max_name_chars:
            config = replace(config, prompts=replace(config.prompts, assistant_name=saved_name))
        personal = PersonalMemory.open(config.memory, paths.memory_dir)
        conversation = ConversationMemory(log=personal.log if personal else None)
        mcp = cls._connected_apps(config, paths.directory)
        service = ChatService(
            config,
            memory=conversation,
            name_store=names,
            personal=personal,
            mcp=mcp,
            mcp_file=paths.directory / config.mcp.file_name,
        )
        keys = KeyStore(paths.keys_file)
        keys.apply()
        setup = ModelSetup(keys, config.installer.ollama_url)
        server = cls(service, token, config.server, ServerInfoFile(paths.info_file), setup)
        logger.info("serving on http://%s:%d", config.server.HOST, config.server.port)
        uvicorn_config = uvicorn.Config(
            server.app,
            host=config.server.HOST,
            port=config.server.port,
            log_config=None,
            access_log=False,
            timeout_graceful_shutdown=cls.GRACEFUL_SHUTDOWN_SECONDS,
        )
        ShutdownAwareServer(uvicorn_config, service).run()

    @property
    def app(self) -> FastAPI:
        """The ASGI application."""
        return self._app

    @asynccontextmanager
    async def _lifespan(self, _app: FastAPI) -> AsyncIterator[None]:
        """Load the model in the background, and publish the server's address."""
        if self._service.state is ServiceState.NEW:
            threading.Thread(
                target=self._service.start, name="tzara-model-load", daemon=True
            ).start()
        pid = os.getpid()
        if self._info_file is not None:
            self._info_file.write(ServerInfo(self._settings.HOST, self._settings.port, pid))
        try:
            yield
        finally:
            self._service.close()
            if self._info_file is not None:
                self._info_file.remove_if_owned_by(pid)

    async def _guard(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Refuse browser requests and requests addressed to a non-loopback host."""
        if "origin" in request.headers:
            return JSONResponse({"detail": "browser requests are not allowed"}, status_code=403)
        if request.headers.get("host", "").lower() not in self._allowed_hosts:
            return JSONResponse({"detail": "invalid Host header"}, status_code=400)
        return await call_next(request)

    def _require_token(self, authorization: str | None = Header(default=None)) -> None:
        """Reject requests without the right bearer token."""
        scheme, _, presented = (authorization or "").partition(" ")
        valid = scheme.lower() == "bearer" and secrets.compare_digest(
            presented.encode(), self._token.encode()
        )
        if not valid:
            raise HTTPException(
                status_code=401,
                detail="missing or invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def _register_routes(self) -> None:
        """Attach the endpoints."""
        protected = [Depends(self._require_token)]
        self._app.add_api_route("/v1/health", self.health, methods=["GET"])
        self._app.add_api_route("/v1/status", self.status, methods=["GET"], dependencies=protected)
        self._app.add_api_route("/v1/models", self.models, methods=["GET"], dependencies=protected)
        self._app.add_api_route(
            "/v1/models/download", self.download_model, methods=["POST"], dependencies=protected
        )
        self._app.add_api_route(
            "/v1/keys/{provider}", self.save_key, methods=["PUT"], dependencies=protected
        )
        self._app.add_api_route("/v1/chat", self.chat, methods=["POST"], dependencies=protected)
        self._app.add_api_route("/v1/write", self.write, methods=["POST"], dependencies=protected)
        self._app.add_api_route(
            "/v1/interrupt", self.interrupt, methods=["POST"], dependencies=protected
        )
        self._app.add_api_route(
            "/v1/conversation/new", self.new_conversation, methods=["POST"], dependencies=protected
        )
        self._app.add_api_route(
            "/v1/model", self.set_model, methods=["PUT"], dependencies=protected
        )
        self._app.add_api_route("/v1/name", self.set_name, methods=["PUT"], dependencies=protected)
        self._app.add_api_route(
            "/v1/conversation", self.conversation, methods=["GET"], dependencies=protected
        )
        self._app.add_api_route("/v1/wake", self.wake, methods=["POST"], dependencies=protected)
        self._app.add_api_route("/v1/apps", self.apps, methods=["GET"], dependencies=protected)
        self._app.add_api_route(
            "/v1/apps", self.add_app, methods=["POST"], status_code=202, dependencies=protected
        )
        self._app.add_api_route(
            "/v1/apps/{name}",
            self.remove_app,
            methods=["DELETE"],
            status_code=202,
            dependencies=protected,
        )
        self._app.add_api_route(
            "/v1/apps/{name}/tools", self.app_tools, methods=["GET"], dependencies=protected
        )
        self._app.add_api_route(
            "/v1/apps/{name}/tools",
            self.set_app_tools,
            methods=["PUT"],
            status_code=202,
            dependencies=protected,
        )
        self._app.add_api_route(
            "/v1/apps/{name}/signin",
            self.start_sign_in,
            methods=["POST"],
            status_code=202,
            dependencies=protected,
        )
        self._app.add_api_route(
            "/v1/apps/{name}/signin", self.sign_in_state, methods=["GET"], dependencies=protected
        )
        self._app.add_api_route("/v1/memory", self.memory, methods=["GET"], dependencies=protected)
        self._app.add_api_route(
            "/v1/conversation/resume/{conversation_id}",
            self.resume_conversation,
            methods=["POST"],
            dependencies=protected,
        )
        self._app.add_api_route(
            "/v1/memory/conversations/{conversation_id}",
            self.memory_conversation,
            methods=["GET"],
            dependencies=protected,
        )
        self._app.add_api_route(
            "/v1/memory/facts/{fact_id}",
            self.forget_fact,
            methods=["DELETE"],
            dependencies=protected,
        )
        self._app.add_api_route(
            "/v1/memory", self.erase_memory, methods=["DELETE"], dependencies=protected
        )

    async def health(self) -> dict[str, str]:
        """Say that the server is up. Needs no token and reveals nothing else."""
        return {"status": "ok"}

    async def status(self) -> dict[str, Any]:
        """Report whether the model is loaded, which one, and whether it is busy."""
        return {
            "state": self._service.state.value,
            "model": self._service.model,
            "name": self._service.name,
            "busy": self._service.busy,
            "error": self._service.error,
        }

    async def models(self) -> dict[str, Any]:
        """List the models Tzara knows about, grouped by provider, with what each still needs."""
        if self._setup is None:
            return {
                "providers": [
                    {
                        "name": provider,
                        "models": [
                            {"id": i, "description": d, "need": "ready"} for i, d in models.items()
                        ],
                    }
                    for provider, models in ModelCatalog.MODELS.items()
                ],
                "downloads": [],
            }
        setup = self._setup
        providers = await asyncio.to_thread(setup.overview)
        downloads = [
            {"model": d.model, "fraction": d.fraction, "error": d.error, "finished": d.finished}
            for d in setup.downloads()
        ]
        return {"providers": providers, "downloads": downloads}

    async def download_model(self, body: DownloadRequest) -> dict[str, Any]:
        """Start downloading a local model; ask ``GET /v1/models`` for how far it has got.

        Raises:
            HTTPException: 422 if the model is not a local one, 501 if downloads are off.
        """
        if self._setup is None:
            raise HTTPException(status_code=501, detail="downloads are not available")
        try:
            download = self._setup.start_download(body.model)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"model": download.model, "fraction": download.fraction}

    async def save_key(self, provider: str, body: KeyRequest) -> dict[str, bool]:
        """Save the API key of a cloud provider.

        Raises:
            HTTPException: 422 if the provider is unknown or the key cannot be one.
        """
        if self._setup is None:
            raise HTTPException(status_code=501, detail="keys are not available")
        try:
            self._setup.keys.save(provider, body.key)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"saved": True}

    async def conversation(self) -> dict[str, Any]:
        """Return the conversation so far (and its id), so a client can show it again."""
        history = await asyncio.to_thread(self._service.history)
        return {
            "id": self._service.conversation_id,
            "messages": [{"role": item.role, "text": item.text} for item in history],
        }

    async def resume_conversation(self, conversation_id: int) -> dict[str, Any]:
        """Carry on an earlier conversation, and return it.

        Raises:
            HTTPException: 404 if there is no such conversation, 409 if a reply is being written.
        """
        try:
            found = await asyncio.to_thread(self._service.resume_conversation, conversation_id)
        except ServiceBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if not found:
            raise HTTPException(status_code=404, detail="no such conversation")
        return await self.conversation()

    async def wake(self) -> JSONResponse:
        """Get the model ready if it has been idle. Returns at once; the work goes on behind."""
        return JSONResponse({"waking": self._service.wake()}, status_code=202)

    async def apps(self) -> dict[str, Any]:
        """List the connected apps, and the ready-made ones that can be added."""
        return await asyncio.to_thread(self._service.apps_overview)

    async def add_app(self, body: AppRequest) -> dict[str, Any]:
        """Add an app, by preset or by its parts. It connects in the background.

        Raises:
            HTTPException: 404 for an unknown preset, 422 if the app is not allowed, 409 if
                Tzara is busy.
        """
        if body.preset:
            preset = McpPresets.find(body.preset)
            if preset is None:
                raise HTTPException(status_code=404, detail="no such ready-made app")
            server = preset.server
            if preset.token_env:
                token = body.token.strip()
                if not token or any(c.isspace() for c in token):
                    raise HTTPException(
                        status_code=422, detail=f"paste the {preset.token_label.lower()} first"
                    )
                server = replace(server, env={**server.env, preset.token_env: token})
        else:
            if not body.command.strip():
                raise HTTPException(status_code=422, detail="an app needs a command")
            server = McpServerConfig(
                name=body.name.strip(),
                command=body.command.strip(),
                args=tuple(body.args),
                env=dict(body.env),
                allow_actions=body.allow_actions,
            )
        try:
            await asyncio.to_thread(self._service.add_app, server)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except ServiceBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return await asyncio.to_thread(self._service.apps_overview)

    async def app_tools(self, name: str) -> dict[str, Any]:
        """Every tool of an app, and whether the model may use it.

        Raises:
            HTTPException: 404 if there is no such app.
        """
        try:
            rows = await asyncio.to_thread(self._service.app_tools, name)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"tools": rows, "limit": self._service.tool_limit}

    async def set_app_tools(self, name: str, body: ToolsRequest) -> dict[str, Any]:
        """Choose the tools of an app the model may use.

        Raises:
            HTTPException: 422 if a tool is unknown or they are too many for the prompt, 409 if
                Tzara is busy.
        """
        try:
            await asyncio.to_thread(self._service.set_app_tools, name, body.tools)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except ServiceBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return await asyncio.to_thread(self._service.apps_overview)

    async def start_sign_in(self, name: str) -> dict[str, Any]:
        """Start signing in to an app. ``GET`` the same address for the link and code.

        Raises:
            HTTPException: 422 if the app has no sign-in.
        """
        try:
            state = await asyncio.to_thread(self._service.start_sign_in, name)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return asdict(state)

    async def sign_in_state(self, name: str) -> dict[str, Any]:
        """How signing in to an app is going."""
        state = self._service.sign_in_state(name)
        return asdict(state) if state is not None else {"running": False, "done": False}

    async def remove_app(self, name: str) -> dict[str, Any]:
        """Remove an app. It disconnects in the background.

        Raises:
            HTTPException: 404 if there is no such app, 409 if Tzara is busy.
        """
        try:
            await asyncio.to_thread(self._service.remove_app, name)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ServiceBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return await asyncio.to_thread(self._service.apps_overview)

    @staticmethod
    def _connected_apps(config: TzaraConfig, directory: Any) -> McpHub | None:
        """The apps listed in the data folder's ``mcp.json``, or ``None`` if there are none."""
        if not config.mcp.enabled:
            return None
        servers, _problems = McpConfigFile.load(directory / config.mcp.file_name)
        return McpHub(config.mcp, servers, directory / ChatService.AUTH_NAME) if servers else None

    async def memory(self) -> dict[str, Any]:
        """List what Tzara remembers about the user, so they can see it."""
        return await asyncio.to_thread(self._service.memory_overview)

    async def memory_conversation(self, conversation_id: int) -> dict[str, Any]:
        """Everything said in one stored conversation."""
        messages = await asyncio.to_thread(self._service.memory_conversation, conversation_id)
        return {"messages": messages}

    async def forget_fact(self, fact_id: int) -> Response:
        """Forget one remembered fact.

        Raises:
            HTTPException: 404 if there is no such fact.
        """
        if not await asyncio.to_thread(self._service.forget_fact, fact_id):
            raise HTTPException(status_code=404, detail="no such fact")
        return Response(status_code=204)

    async def erase_memory(self) -> Response:
        """Forget everything: all facts, all stored conversations, and the current one.

        Raises:
            HTTPException: 409 if a reply is being written.
        """
        try:
            await asyncio.to_thread(self._service.erase_memory)
        except ServiceBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return Response(status_code=204)

    async def write(self, body: WriteBody) -> dict[str, Any]:
        """Write text for a field of another app: what to put there and what it replaces.

        Raises:
            HTTPException: 404 if writing is off, 503 while the model loads, 409 if a reply is
                being written, 502 if the model wrote nothing.
        """
        try:
            result = await asyncio.to_thread(self._service.write, WriteRequest(**body.model_dump()))
        except ServiceNotReady as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except ServiceBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {
            "text": result.text,
            "mode": result.mode,
            "place": result.place,
            "replaces": result.replaces,
            "changed": result.changed,
        }

    async def chat(self, body: ChatRequest) -> StreamingResponse:
        """Answer a message, streaming the reply as server-sent events.

        Raises:
            HTTPException: 413 if the message is too long, 503 while the model is
                loading, 409 if another reply is being written.
        """
        if len(body.message) > self._settings.max_message_chars:
            raise HTTPException(
                status_code=413,
                detail=f"message is longer than {self._settings.max_message_chars} characters",
            )

        loop = asyncio.get_running_loop()
        events: asyncio.Queue[TurnEvent | None] = asyncio.Queue()

        def sink(event: TurnEvent | None) -> None:
            # Runs on the turn's thread. The loop is gone if the server is
            # shutting down, and then there is nobody left to tell.
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(events.put_nowait, event)

        try:
            context = ScreenContext(**body.context.model_dump()) if body.context else None
            turn = self._service.start_turn(body.message, sink, context, body.place.strip())
        except ServiceNotReady as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except ServiceBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

        async def stream() -> AsyncIterator[str]:
            finished = False
            try:
                while True:
                    try:
                        async with asyncio.timeout(self._settings.heartbeat_seconds):
                            event = await events.get()
                    except TimeoutError:
                        yield self.KEEP_ALIVE
                        continue
                    if event is None:
                        break
                    yield self._format_event(event)
                finished = True
            finally:
                # The client went away mid-reply: stop writing it.
                if not finished:
                    turn.cancel()

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers=self.STREAM_HEADERS
        )

    async def interrupt(self) -> Response:
        """Cancel the reply being written. Does nothing if there is none."""
        self._service.interrupt()
        return Response(status_code=204)

    async def new_conversation(self) -> dict[str, bool]:
        """Forget the conversation so far.

        Raises:
            HTTPException: 409 if the agent is busy.
        """
        try:
            await asyncio.to_thread(self._service.new_conversation)
        except ServiceBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"ok": True}

    async def set_model(self, body: ModelRequest) -> dict[str, str]:
        """Load another model and switch to it, keeping the conversation.

        Raises:
            HTTPException: 422 if the name is not ``provider:model-name``, 409 if
                the agent is busy, 502 if the model could not be loaded.
        """
        if not ModelCatalog.provider_of(body.model):
            raise HTTPException(
                status_code=422, detail="model must be written as provider:model-name"
            )
        try:
            await asyncio.to_thread(self._service.switch_model, body.model)
        except ServiceBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except Exception as error:
            logger.exception("could not switch to %s", body.model)
            raise HTTPException(
                status_code=502, detail=f"could not load {body.model}: {error}"
            ) from error
        return {"model": self._service.model}

    async def set_name(self, body: NameRequest) -> dict[str, str]:
        """Rename the assistant. This reloads the agent, which takes a few seconds.

        Raises:
            HTTPException: 422 if the name is not one short line, 409 if the agent is
                busy, 502 if the agent could not be reloaded.
        """
        try:
            await asyncio.to_thread(self._service.rename, body.name)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except ServiceBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except Exception as error:
            logger.exception("could not rename the assistant")
            raise HTTPException(status_code=502, detail=f"could not rename: {error}") from error
        return {"name": self._service.name}

    @staticmethod
    def _format_event(event: TurnEvent) -> str:
        """Render one turn event as a server-sent event."""
        data: dict[str, Any]
        if isinstance(event, TokenEvent):
            name, data = "token", {"text": event.text}
        elif isinstance(event, StatusEvent):
            name, data = "status", {"text": event.text}
        elif isinstance(event, ToolEvent):
            name, data = "tool", {"name": event.name, "state": event.state}
        elif isinstance(event, DoneEvent):
            name = "done"
            data = {
                "first_token_seconds": event.first_token_seconds,
                "characters": event.characters,
            }
        else:
            name, data = "error", {"message": event.message}
        return f"event: {name}\ndata: {json.dumps(data)}\n\n"
