"""Minimal FastAPI-style routing on plain Starlette (no pydantic).

Why: the backend also runs embedded in the Android app, where pydantic-core has no build, so
FastAPI cannot be used. This module implements the small subset the TITAN API needs:
path/query parameters, `Depends` (incl. generator dependencies, cached per request),
`Body(embed=True)`, whole-body models, `Form`/`File`, route-level `dependencies=[...]`,
`HTTPException`, and a tiny validated `BaseModel` with `Field` constraints.
"""
from __future__ import annotations

import inspect
import json
import re
import types
import typing
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Callable

from anyio import to_thread
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

__all__ = ["APIRouter", "BaseModel", "Body", "Depends", "Field", "File", "Form", "HTTPException", "Request",
           "UploadFile", "ValidationError", "json_response"]


class HTTPException(Exception):
    def __init__(self, status_code: int, detail: Any = None):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


class ValidationError(ValueError):
    pass


_MISSING = object()


@dataclass
class _Dep:
    fn: Callable


def Depends(fn: Callable) -> Any:  # noqa: N802
    return _Dep(fn)


@dataclass
class _Body:
    default: Any = _MISSING
    embed: bool = False


def Body(default: Any = _MISSING, embed: bool = False) -> Any:  # noqa: N802
    return _Body(default, embed)


@dataclass
class _Form:
    default: Any = _MISSING


def Form(default: Any = _MISSING) -> Any:  # noqa: N802
    return _Form(default)


def File(default: Any = _MISSING) -> Any:  # noqa: N802
    return _Form(default)


# ------------------------------------------------------------------------------- models
@dataclass
class _FieldInfo:
    default: Any = _MISSING
    gt: float | None = None
    ge: float | None = None
    lt: float | None = None
    le: float | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None


def Field(default: Any = _MISSING, *, gt=None, ge=None, lt=None, le=None, min_length=None, max_length=None,  # noqa: N802
          pattern=None) -> Any:
    return _FieldInfo(default, gt, ge, lt, le, min_length, max_length, pattern)


def _optional(tp) -> tuple[Any, bool]:
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        return (args[0] if len(args) == 1 else Any), type(None) in typing.get_args(tp)
    return tp, tp is Any


def _coerce(name: str, value: Any, tp: Any) -> Any:
    inner, nullable = _optional(tp)
    if value is None:
        if nullable:
            return None
        raise ValidationError(f"{name}: value required")
    origin = typing.get_origin(inner) or inner
    try:
        if inner is Any:
            return value
        if origin is bool:
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if origin is int:
            if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
                raise ValueError
            return int(value)
        if origin is float:
            if isinstance(value, bool):
                raise ValueError
            return float(value)
        if origin is str:
            if not isinstance(value, (str, int, float)):
                raise ValueError
            return str(value)
        if origin is dict:
            if not isinstance(value, dict):
                raise ValueError
            return value
        if origin is list:
            if not isinstance(value, list):
                raise ValueError
            args = typing.get_args(inner)
            return [_coerce(name, v, args[0]) for v in value] if args else value
        if inspect.isclass(origin) and issubclass(origin, Enum):
            return origin(value)
        if inspect.isclass(origin) and issubclass(origin, BaseModel):
            return origin.model_validate(value)
    except (TypeError, ValueError) as e:
        raise ValidationError(f"{name}: invalid value {value!r}") from e
    return value


class BaseModel:
    """Validated record: annotations + defaults / Field(...) constraints."""

    def __init__(self, **data):
        hints = typing.get_type_hints(type(self))
        unknown = set(data) - set(hints)
        _ = unknown  # extra keys are ignored (lenient like FastAPI's default)
        for name, tp in hints.items():
            if name.startswith("_"):
                continue
            spec = getattr(type(self), name, _MISSING)
            fi = spec if isinstance(spec, _FieldInfo) else _FieldInfo(spec)
            if name in data:
                v = _coerce(name, data[name], tp)
            elif fi.default is not _MISSING:
                v = fi.default() if callable(fi.default) and not isinstance(fi.default, type) else fi.default
                v = dict(v) if isinstance(v, dict) else v
            else:
                raise ValidationError(f"{name}: field required")
            if v is not None:
                for attr, ok in (("gt", lambda a, b: a > b), ("ge", lambda a, b: a >= b), ("lt", lambda a, b: a < b),
                                 ("le", lambda a, b: a <= b)):
                    lim = getattr(fi, attr)
                    if lim is not None and not ok(v, lim):
                        raise ValidationError(f"{name}: must be {attr} {lim}")
                if isinstance(v, str):
                    if fi.min_length is not None and len(v) < fi.min_length:
                        raise ValidationError(f"{name}: too short")
                    if fi.max_length is not None and len(v) > fi.max_length:
                        raise ValidationError(f"{name}: too long")
                    if fi.pattern and not re.match(fi.pattern, v):
                        raise ValidationError(f"{name}: invalid format")
            setattr(self, name, v)

    @classmethod
    def model_validate(cls, data: Any):
        if not isinstance(data, dict):
            raise ValidationError("request body must be a JSON object")
        return cls(**data)

    def model_dump(self, exclude: set | None = None, exclude_none: bool = False) -> dict:
        out = {k: getattr(self, k) for k in typing.get_type_hints(type(self)) if not k.startswith("_")}
        out = {k: v for k, v in out.items() if k not in (exclude or set())}
        return {k: v for k, v in out.items() if not (exclude_none and v is None)}


# ------------------------------------------------------------------------------- routing
def _default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, BaseModel):
        return o.model_dump()
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def json_response(data: Any, status: int = 200) -> Response:
    return Response(json.dumps(data, default=_default), status_code=status, media_type="application/json")


class _Ctx:
    def __init__(self, request: Request, body: bytes, form):
        self.request, self.body, self.form = request, body, form
        self.cache: dict = {}
        self.gens: list = []
        self._json = _MISSING

    def json(self):
        if self._json is _MISSING:
            try:
                self._json = json.loads(self.body) if self.body else {}
            except json.JSONDecodeError as e:
                raise HTTPException(422, f"invalid JSON body: {e}") from e
        return self._json


def _call_dep(fn: Callable, ctx: _Ctx):
    if fn in ctx.cache:
        return ctx.cache[fn]
    kwargs = _resolve(fn, ctx, path_params={})
    if inspect.isgeneratorfunction(fn):
        gen = fn(**kwargs)
        val = next(gen)
        ctx.gens.append(gen)
    else:
        val = fn(**kwargs)
    ctx.cache[fn] = val
    return val


def _resolve(fn: Callable, ctx: _Ctx, path_params: dict) -> dict:
    sig = inspect.signature(fn)
    hints = typing.get_type_hints(fn, include_extras=False)
    q = ctx.request.query_params
    kwargs = {}
    for name, p in sig.parameters.items():
        tp = hints.get(name, Any)
        d = p.default
        if tp is Request or (inspect.isclass(tp) and issubclass(tp, Request)):
            kwargs[name] = ctx.request
        elif name == "raw_body":
            kwargs[name] = ctx.body
        elif isinstance(d, _Dep):
            kwargs[name] = _call_dep(d.fn, ctx)
        elif name in path_params:
            kwargs[name] = _coerce(name, path_params[name], tp)
        elif isinstance(d, _Body):
            data = ctx.json()
            if d.embed:
                if isinstance(data, dict) and name in data:
                    kwargs[name] = _coerce(name, data[name], tp)
                elif d.default is not _MISSING:
                    kwargs[name] = d.default
                else:
                    raise ValidationError(f"{name}: field required in body")
            else:
                kwargs[name] = data if data != {} or d.default is _MISSING else d.default
        elif isinstance(d, _Form):
            if ctx.form is None or name not in ctx.form:
                if d.default is _MISSING:
                    raise ValidationError(f"{name}: form field required")
                kwargs[name] = d.default
            else:
                v = ctx.form[name]
                kwargs[name] = v if isinstance(v, UploadFile) else _coerce(name, v, tp)
        elif inspect.isclass(_optional(tp)[0]) and issubclass(_optional(tp)[0], BaseModel):
            kwargs[name] = _optional(tp)[0].model_validate(ctx.json())
        else:
            if name in q:
                kwargs[name] = _coerce(name, q[name], tp)
            elif d is not inspect.Parameter.empty:
                kwargs[name] = d
            else:
                raise ValidationError(f"{name}: query parameter required")
    return kwargs


class APIRouter:
    def __init__(self, prefix: str = ""):
        self.prefix = prefix
        self.entries: list[tuple[str, str, Callable, list]] = []

    def _add(self, method: str, path: str, dependencies: list | None):
        def deco(fn):
            self.entries.append((method, self.prefix + path, fn, dependencies or []))
            return fn
        return deco

    def get(self, path, dependencies=None):
        return self._add("GET", path, dependencies)

    def post(self, path, dependencies=None):
        return self._add("POST", path, dependencies)

    def patch(self, path, dependencies=None):
        return self._add("PATCH", path, dependencies)

    def delete(self, path, dependencies=None):
        return self._add("DELETE", path, dependencies)

    def routes(self) -> list[Route]:
        return [Route(path, _endpoint(fn, deps), methods=[method], name=fn.__name__)
                for method, path, fn, deps in self.entries]

    def describe(self) -> list[tuple[str, str, str]]:
        return [(m, p, (fn.__doc__ or fn.__name__.replace("_", " ")).strip().splitlines()[0])
                for m, p, fn, _ in self.entries]


def _endpoint(fn: Callable, deps: list):
    if inspect.iscoroutinefunction(fn):
        raise TypeError(f"{fn.__name__}: handlers must be synchronous (they run in a worker thread)")

    async def endpoint(request: Request) -> Response:
        ctype = request.headers.get("content-type", "")
        form = await request.form() if ("multipart/form-data" in ctype or "urlencoded" in ctype) else None
        body = b"" if form is not None else await request.body()
        ctx = _Ctx(request, body, form)

        def run() -> Response:
            # dependencies, handler and dependency finalisation run in ONE worker thread, so a
            # thread-bound DB session is opened and closed on the same thread
            try:
                for d in deps:
                    _call_dep(d.fn, ctx)
                result = fn(**_resolve(fn, ctx, dict(request.path_params)))
                resp = result if isinstance(result, Response) else json_response(result)
            except HTTPException as e:
                _finalise(ctx, e)
                return json_response({"detail": e.detail}, e.status_code)
            except ValueError as e:   # ValidationError, SignalError, enum errors
                _finalise(ctx, e)
                return json_response({"detail": str(e)}, 422)
            except BaseException as e:  # noqa: BLE001
                _finalise(ctx, e)
                raise
            _finalise(ctx, None)
            return resp

        return await to_thread.run_sync(run)

    return endpoint


def _finalise(ctx: _Ctx, error: BaseException | None):
    for gen in reversed(ctx.gens):
        try:
            if error is None:
                next(gen)
            else:
                gen.throw(error)
        except StopIteration:
            pass
        except BaseException as e:  # noqa: BLE001
            if e is not error:
                raise
