"""ロギング初期化と未捕捉例外フック."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import traceback

from .paths import LOG_FILE


_LOGGER = logging.getLogger("kennybot.bootstrap")


def _format_exc(exc_type: type[BaseException], exc: BaseException, tb) -> str:
    return "".join(traceback.format_exception(exc_type, exc, tb))


def _log_unhandled(prefix: str, exc_type: type[BaseException], exc: BaseException, tb) -> None:
    _LOGGER.critical("%s\n%s", prefix, _format_exc(exc_type, exc, tb))


def setup_logging() -> None:
    """ロギング設定を初期化する."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )
    logging.captureWarnings(True)

    def _sys_excepthook(exc_type: type[BaseException], exc: BaseException, tb) -> None:
        _log_unhandled("Unhandled exception in main thread", exc_type, exc, tb)

    def _threading_excepthook(args: threading.ExceptHookArgs) -> None:
        _log_unhandled(
            f"Unhandled exception in thread {args.thread.name if args.thread else 'unknown'}",
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
        )

    sys.excepthook = _sys_excepthook
    threading.excepthook = _threading_excepthook


def install_asyncio_exception_handler(loop: asyncio.AbstractEventLoop) -> None:
    """asyncio の未処理例外をログへ流す."""

    def _handler(_loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
        message = str(context.get("message") or "Unhandled asyncio exception")
        exception = context.get("exception")
        future = context.get("future")
        task = context.get("task")
        details: list[str] = [message]
        if task is not None:
            details.append(f"task={task!r}")
        if future is not None:
            details.append(f"future={future!r}")
        if isinstance(exception, BaseException):
            _LOGGER.critical("%s\n%s", " | ".join(details), "".join(traceback.format_exception(type(exception), exception, exception.__traceback__)))
        else:
            _LOGGER.critical("%s | context=%r", " | ".join(details), context)

    loop.set_exception_handler(_handler)


def get_logger(name: str) -> logging.Logger:
    """ロガーを取得する."""
    return logging.getLogger(name)
