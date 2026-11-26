"""
obs_logging_alexis.py  --  structured JSON logging of every tool call (PHYS 7440, HW10).

One module, used by every entry point of the project:

  data_pipeline_alexis.py   component "pipeline"     logs/pipeline_alexis.jsonl
  mcp_server_alexis.py      component "mcp_server"   logs/mcp_server_alexis.jsonl
  hw8/eval_harness.py       component "eval_harness" logs/eval_harness_alexis.jsonl
  make_key_result_alexis.py component "key_result"   results/key_result_run_alexis.jsonl

Each log line is one JSON object written by ``python-json-logger``.  A tool call produces
exactly one line with ``"event": "tool_call"`` and these fields:

  timestamp       ISO-8601 UTC time the call *finished*
  tool            what was called, e.g. "data_pipeline.download" or "mcp.estimate_tilt"
  input_args      the arguments, sanitised (API keys / tokens redacted, arrays summarised
                  as shape + dtype, absolute paths made project-relative)
  output_summary  a structured summary of the return value (dicts kept, lists cut to 20
                  items, strings to 200 characters)
  output_preview  the first 200 characters of that summary as JSON text
  elapsed_ms      wall time of the call in milliseconds
  status          "success" or "failure"
  error_type, error_message   set when status is "failure"
  run_id, seq, parent_seq, depth   which process run this was, the call's number in that
                  run, and the call it happened inside: a reviewer can rebuild the call tree
                  of a run from the log alone (see ``trace_alexis.py``).

Every run also writes a ``run_start`` line (command line, seeds, Python and package
versions, git commit) and a ``run_end`` line (number of calls and failures).  Ordinary log
messages of the pipeline (retries, missing fields) go to the same file with
``"event": "message"`` and the ``parent_seq`` of the call they happened in.

Usage
-----
    import obs_logging_alexis as obs
    obs.seed_everything()                       # random.seed(42), numpy.random.seed(42)
    obs.configure("pipeline", project_root=...)

    @obs.traced("data_pipeline.download")       # decorator
    def download(url, dest): ...

    obs.call("firstmap_b.estimate_from_maps", fm.estimate_from_maps, Q, U, p, cos2, truth)

    with obs.span("anthropic.messages.create", {"model": m}) as sp:   # inline block
        resp = client.messages.create(...)
        sp.output = resp

When ``configure`` has not been called, the decorators cost one attribute check and log
nothing, so importing a module from a notebook or a test does not create log files.
"""

import contextlib
import contextvars
import datetime as _dt
import functools
import inspect
import itertools
import json
import logging
import math
import os
import platform
import random
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

try:                                    # python-json-logger >= 3.1
    from pythonjsonlogger.json import JsonFormatter
except ImportError:                     # older releases
    from pythonjsonlogger.jsonlogger import JsonFormatter

import numpy as np

SEED = 42
MAX_STR = 200          # characters kept of any string, and of output_preview
MAX_ITEMS = 20         # items kept of any list / dict
MAX_DEPTH = 5          # nesting kept of any structure
SMALL_ARRAY = 8        # arrays up to this many elements are logged in full

TOOL_LOGGER = logging.getLogger("toolcalls")
TOOL_LOGGER.propagate = False
TOOL_LOGGER.setLevel(logging.INFO)

RUN_ID = (_dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
          + "-" + uuid.uuid4().hex[:6])       # os.urandom: not affected by seed_everything

_state = {"component": None, "path": None, "handler": None, "root": None,
          "n_calls": 0, "n_fail": 0, "seeds": None}
_seq = itertools.count(1)
_lock = threading.Lock()
_stack = contextvars.ContextVar("obs_stack", default=())


# ====================================================================== seeds
def seed_everything(seed=SEED):
    """Seed the two *global* generators: Python's ``random`` and NumPy's legacy
    ``numpy.random``.  Returns what was set (logged in ``run_start``).

    The project's own random numbers come from local ``numpy.random.default_rng(seed)``
    generators with fixed seeds, which this call does not touch; REPRODUCIBILITY.md lists
    each of them.  Seeding the globals still matters: it fixes tenacity's retry jitter
    (``random``) and any library that draws from ``numpy.random`` without its own seed.
    """
    random.seed(seed)
    np.random.seed(seed)
    _state["seeds"] = {"python_random": seed, "numpy_random_legacy_global": seed,
                       "PYTHONHASHSEED": os.environ.get("PYTHONHASHSEED", "unset")}
    return _state["seeds"]


# ====================================================================== sanitising
_SECRET_KEY = re.compile(r"(^|[_\-.])(api[_\-]?key|access[_\-]?token|auth[_\-]?token|"
                         r"refresh[_\-]?token|token|secret|client[_\-]?secret|password|passwd|"
                         r"authorization|cookie|credentials?|private[_\-]?key|signing[_\-]?key)$",
                         re.I)
_SECRET_VALUES = [re.compile(p) for p in (
    r"sk-ant-[A-Za-z0-9_\-]{8,}",                 # Anthropic
    r"\bsk-[A-Za-z0-9_\-]{16,}",                  # OpenAI-style
    r"\bgh[pousr]_[A-Za-z0-9]{16,}",              # GitHub tokens
    r"\bgithub_pat_[A-Za-z0-9_]{16,}",
    r"(?i)\bbearer\s+[A-Za-z0-9._\-~+/=]+",       # Authorization: Bearer ...
)]
_SECRET_ENV = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN", "GH_TOKEN", "HF_TOKEN")
REDACTED = "[REDACTED]"
_roots = {"project": None, "home": str(Path.home())}


def set_project_root(path):
    """Absolute paths under this folder are logged relative to it (no local paths in logs)."""
    _roots["project"] = str(Path(path).resolve()) if path else None


def is_secret_key(key):
    """True for a dict key / argument name that holds a credential."""
    return bool(_SECRET_KEY.search(str(key)))


def scrub(text):
    """Redact credentials inside a string and shorten local paths."""
    s = str(text)
    for var in _SECRET_ENV:
        val = os.environ.get(var)
        if val and len(val) >= 8 and val in s:
            s = s.replace(val, REDACTED)
    for pat in _SECRET_VALUES:
        s = pat.sub(REDACTED, s)
    proj = _roots["project"]
    if proj and proj in s:
        s = s.replace(proj + os.sep, "").replace(proj, ".")
    home = _roots["home"]
    if home and len(home) > 1 and home in s:
        s = s.replace(home, "~")
    return s


def _short(s, n=MAX_STR):
    return s if len(s) <= n else s[:n] + f"...(+{len(s) - n} chars)"


def summarize(obj, depth=0):
    """A JSON-safe, size-bounded, credential-free summary of any value."""
    if obj is None or isinstance(obj, (bool, int)) and not isinstance(obj, np.bool_):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else str(obj)          # JSON has no NaN / inf
    if isinstance(obj, np.generic):
        return summarize(obj.item(), depth)
    if isinstance(obj, str):
        return _short(scrub(obj))
    if isinstance(obj, (bytes, bytearray)):
        return {"type": "bytes", "len": len(obj)}
    if isinstance(obj, Path):
        return _short(scrub(str(obj)))
    if isinstance(obj, np.ndarray):
        out = {"type": "ndarray", "shape": list(obj.shape), "dtype": str(obj.dtype)}
        if obj.size <= SMALL_ARRAY:
            out["values"] = summarize(obj.tolist(), depth + 1)
        return out
    if depth >= MAX_DEPTH:
        return f"<{type(obj).__name__}>"
    if hasattr(obj, "status_code") and hasattr(obj, "headers") and hasattr(obj, "url"):
        return {"type": "http_response", "status_code": obj.status_code,           # requests
                "url": summarize(str(obj.url), depth + 1),
                "content_length": obj.headers.get("Content-Length")}
    if isinstance(obj, dict):
        out = {}
        for i, (k, v) in enumerate(obj.items()):
            if i >= MAX_ITEMS:
                out["..."] = f"+{len(obj) - MAX_ITEMS} more keys"
                break
            k = str(k)
            out[k] = REDACTED if is_secret_key(k) and v not in (None, "") else summarize(v, depth + 1)
        return out
    if isinstance(obj, (list, tuple, set, frozenset)):
        seq = sorted(obj, key=repr) if isinstance(obj, (set, frozenset)) else list(obj)
        out = [summarize(v, depth + 1) for v in seq[:MAX_ITEMS]]
        if len(seq) > MAX_ITEMS:
            out.append(f"...(+{len(seq) - MAX_ITEMS} more items, {len(seq)} total)")
        return out
    if hasattr(obj, "__dict__") and type(obj).__name__ == "Namespace":           # argparse
        return summarize(vars(obj), depth)
    if hasattr(obj, "model_dump"):                                               # pydantic / SDK
        try:
            return summarize(obj.model_dump(), depth)
        except Exception:
            pass
    return f"<{type(obj).__name__}>"


def preview(summary):
    """First MAX_STR characters of a summary as JSON text."""
    return _short(json.dumps(summary, default=str, sort_keys=False), MAX_STR)


def bind_args(fn, args, kwargs):
    """Map positional and keyword arguments to parameter names (defaults included)."""
    try:
        ba = inspect.signature(fn).bind_partial(*args, **kwargs)
        ba.apply_defaults()
        return dict(ba.arguments)
    except (TypeError, ValueError):
        return {"args": list(args), "kwargs": kwargs}


# ====================================================================== configuration
class _ContextFilter(logging.Filter):
    """Adds component, run_id and the current call to every record of the handler."""

    def filter(self, record):
        record.timestamp = _dt.datetime.fromtimestamp(record.created, _dt.timezone.utc
                                                      ).isoformat(timespec="milliseconds")
        record.component = _state["component"]
        record.run_id = RUN_ID
        if not hasattr(record, "event"):
            record.event = "message"
            stack = _stack.get()
            record.parent_seq = stack[-1] if stack else None
            record.msg = scrub(record.getMessage())
            record.args = ()
        return True


def _git_info():
    here = Path(__file__).resolve().parent
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=here, capture_output=True,
                             text=True, timeout=5).stdout.strip() or None
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                               cwd=here, capture_output=True, text=True, timeout=5).stdout.strip()
        return {"commit": sha, "uncommitted_changes": bool(dirty)}
    except Exception:
        return {"commit": None, "uncommitted_changes": None}


def _versions():
    from importlib.metadata import PackageNotFoundError, version
    out = {"python": platform.python_version(), "platform": platform.platform()}
    for name in ("numpy", "scipy", "astropy", "h5py", "matplotlib", "anthropic", "mcp",
                 "tenacity", "requests", "python-json-logger"):
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            pass
    return out


def default_log_path(component, project_root=None):
    """``$OBS_LOG_FILE`` if set, else ``<project>/logs/<component>_alexis.jsonl``."""
    if os.environ.get("OBS_LOG_FILE"):
        return Path(os.environ["OBS_LOG_FILE"])
    root = Path(project_root) if project_root else Path.cwd()
    return root / "logs" / f"{component}_alexis.jsonl"


def configure(component, log_file=None, project_root=None, capture_root=True):
    """Send tool-call records (and, with ``capture_root``, ordinary log messages) to a
    JSON-lines file.  Calling it again with the same file does nothing; with another file
    it moves the handler.  Returns the log path."""
    if project_root:
        set_project_root(project_root)
    path = Path(log_file) if log_file else default_log_path(component, project_root)
    path = path.resolve()
    if _state["handler"] is not None and _state["path"] == path and _state["component"] == component:
        return path
    shutdown(write_end=_state["handler"] is not None)
    path.parent.mkdir(parents=True, exist_ok=True)
    h = logging.FileHandler(path, mode="a", encoding="utf-8")
    h.setLevel(logging.INFO)
    h.setFormatter(JsonFormatter("%(timestamp)s %(levelname)s %(name)s %(message)s",
                                 rename_fields={"levelname": "level", "name": "logger"},
                                 json_default=str))
    h.addFilter(_ContextFilter())
    TOOL_LOGGER.addHandler(h)
    if capture_root:
        logging.getLogger().addHandler(h)
    _state.update(component=component, path=path, handler=h, root=capture_root,
                  n_calls=0, n_fail=0)
    TOOL_LOGGER.info("run_start", extra={
        "event": "run_start", "argv": summarize(sys.argv), "cwd": scrub(os.getcwd()),
        "seeds": _state["seeds"], "versions": _versions(), "git": _git_info(),
        "parent_run_id": os.environ.get("OBS_PARENT_RUN_ID"), "pid": os.getpid()})
    return path


def shutdown(write_end=True):
    """Write ``run_end`` and detach the handler (also registered with atexit)."""
    h = _state["handler"]
    if h is None:
        return
    if write_end:
        TOOL_LOGGER.info("run_end", extra={"event": "run_end", "n_tool_calls": _state["n_calls"],
                                           "n_failures": _state["n_fail"]})
    TOOL_LOGGER.removeHandler(h)
    logging.getLogger().removeHandler(h)
    h.close()
    _state.update(handler=None, path=None)


import atexit                                          # noqa: E402
atexit.register(shutdown)


def enabled():
    """True once ``configure`` has attached a handler."""
    return _state["handler"] is not None


def log_path():
    return _state["path"]


# ====================================================================== spans
class Span:
    """What a ``span`` block reports: set ``output`` (and optionally ``fail(msg)``)."""
    __slots__ = ("output", "failed", "fail_message", "extra")

    def __init__(self):
        self.output, self.failed, self.fail_message, self.extra = None, False, None, {}

    def fail(self, message):
        """Mark the call as failed without raising (e.g. an API that returns an error)."""
        self.failed, self.fail_message = True, str(message)


@contextlib.contextmanager
def span(tool, input_args=None):
    """Time a block and write one ``tool_call`` record for it, success or failure.

    Exceptions are recorded and re-raised unchanged.
    """
    if not enabled():
        yield Span()
        return
    with _lock:
        seq = next(_seq)
    stack = _stack.get()
    token = _stack.set(stack + (seq,))
    sp = Span()
    t0 = time.perf_counter()
    err = None
    try:
        yield sp
    except BaseException as e:            # noqa: BLE001  record, then re-raise
        err = e
        raise
    finally:
        _stack.reset(token)
        elapsed = (time.perf_counter() - t0) * 1000.0
        failed = err is not None or sp.failed
        summ = summarize(sp.output) if err is None else None
        rec = {"event": "tool_call", "tool": tool, "seq": seq,
               "parent_seq": stack[-1] if stack else None, "depth": len(stack),
               "input_args": summarize(input_args or {}),
               "output_summary": summ, "output_preview": preview(summ) if err is None else None,
               "elapsed_ms": round(elapsed, 3), "status": "failure" if failed else "success",
               "error_type": type(err).__name__ if err is not None else ("ReportedError" if failed else None),
               "error_message": _short(scrub(str(err))) if err is not None else
               (_short(scrub(sp.fail_message)) if failed else None)}
        rec.update(sp.extra)
        with _lock:
            _state["n_calls"] += 1
            _state["n_fail"] += int(failed)
        TOOL_LOGGER.log(logging.WARNING if failed else logging.INFO, "tool_call", extra=rec)


def traced(tool=None, skip_args=()):
    """Decorator: log every call of the function as a tool call.

    ``skip_args`` names parameters left out of ``input_args`` (e.g. a large object that is
    already identified by another argument).  Works on plain and ``async`` functions.
    """
    def deco(fn):
        name = tool or f"{fn.__module__}.{fn.__qualname__}"

        def _inputs(a, k):
            d = bind_args(fn, a, k)
            return {key: v for key, v in d.items() if key not in skip_args}

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def awrapper(*a, **k):
                if not enabled():
                    return await fn(*a, **k)
                with span(name, _inputs(a, k)) as sp:
                    sp.output = await fn(*a, **k)
                    return sp.output
            return awrapper

        @functools.wraps(fn)
        def wrapper(*a, **k):
            if not enabled():
                return fn(*a, **k)
            with span(name, _inputs(a, k)) as sp:
                sp.output = fn(*a, **k)
                return sp.output
        return wrapper
    return deco


def call(tool, fn, *args, **kwargs):
    """Call ``fn(*args, **kwargs)`` as a logged tool call (for functions from other modules
    that are not decorated, such as the physics code in firstmap_b.py)."""
    if not enabled():
        return fn(*args, **kwargs)
    with span(tool, bind_args(fn, args, kwargs)) as sp:
        sp.output = fn(*args, **kwargs)
        return sp.output


def event(name, **fields):
    """Write a non-call record (e.g. a scored evaluation item)."""
    if enabled():
        stack = _stack.get()
        TOOL_LOGGER.info(name, extra={"event": name, "parent_seq": stack[-1] if stack else None,
                                      **{k: summarize(v) for k, v in fields.items()}})
