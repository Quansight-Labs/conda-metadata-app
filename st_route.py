"""
Taken from https://gist.github.com/schaumb/d557dabf0beced7dfaa1be7acc09b1e4
See https://github.com/streamlit/streamlit/issues/439
"""
import functools
import gc
import weakref
from typing import Optional, Callable, Union
from weakref import WeakSet

from streamlit import config
from streamlit.runtime import Runtime
from streamlit.runtime.scriptrunner import get_script_run_ctx, add_script_run_ctx
from streamlit.web.server.server_util import make_url_path_regex
from tornado import httputil
from tornado.httputil import HTTPServerRequest, ResponseStartLine, HTTPHeaders
from tornado.routing import Rule, AnyMatches, ReversibleRuleRouter
from tornado.web import Application


class _RouteRegister:
    @staticmethod
    def handler(self: HTTPServerRequest, path_args, path_kwargs, func: Callable,
                ctx: weakref.ref):
        old = get_script_run_ctx()
        add_script_run_ctx(ctx=ctx())
        res = func(path_args=path_args,
                   path_kwargs=path_kwargs,
                   method=self.method,
                   body=self.body,
                   arguments=self.arguments)
        add_script_run_ctx(ctx=old)

        response_code: int = 200
        response_body: Optional[Union[bytes, str]] = None
        headers = HTTPHeaders()

        if not isinstance(res, tuple):
            if isinstance(res, int):
                response_code = res
            elif isinstance(res, bytes) or isinstance(res, str):
                response_body = res
            elif res is not None:
                raise TypeError('Unknown return type from handler.')
        else:
            if len(res) == 2:
                response_code, response_body = res
                
            elif len(res) == 3:
                response_code, response_body, header = res
                for header_key, header_val in header.items():
                    headers.add(header_key, header_val)
            else:
                raise TypeError('Unknown return type from handler.')

        if isinstance(response_body, str):
          response_body = response_body.encode()

        if response_body is not None:
            headers.add("Content-Length", str(len(response_body)))

        self.connection.write_headers(
            ResponseStartLine(self.version, response_code, httputil.responses.get(response_code, "Unknown")),
            headers,
        )

        self.connection.write(response_body)
        self.connection.finish()

    @classmethod
    def instance(cls) -> '_RouteRegister':
        inst: Runtime = Runtime.instance()
        res: Optional[_RouteRegister] = getattr(inst, '_streamlit_route_register', None)
        if res is None:
            app: Application = next(iter((k for k in gc.get_referrers(Application) if isinstance(k, Application))))

            res = _RouteRegister()
            app.add_handlers(".*", [Rule(AnyMatches(), res._the_rules)])
            setattr(inst, '_streamlit_route_register', res)
        return res

    def __init__(self):
        self._the_rules: ReversibleRuleRouter = ReversibleRuleRouter([])
        self._the_rules.rules = WeakSet()
        setattr(self._the_rules.rules, 'append', getattr(self._the_rules.rules, 'add'))
        self._deregists = {}

    @staticmethod
    def _get_full_path(path: str, globally: bool, trailing_slash: bool, session_id: str) -> str:
        return make_url_path_regex(config.get_option("server.baseUrlPath"),
                                   *(() if globally else (session_id,)),
                                   path,
                                   trailing_slash=trailing_slash)

    def regist_or_replace(self, path: str, globally: bool, trailing_slash: bool, f: Callable):
        ctx = get_script_run_ctx()
        session_id = ctx.session_id
        full_path = _RouteRegister._get_full_path(path, globally, trailing_slash, session_id)

        self._the_rules.add_rules(
            [(full_path, functools.partial(_RouteRegister.handler, func=f, ctx=weakref.ref(ctx)), {}, full_path)]
        )

        def dereg(missing_client):
            for obj in self._deregists.pop(session_id, None)[0]:
                obj()

        client = Runtime.instance().get_client(session_id)
        self._deregists.setdefault(session_id, (set(), weakref.proxy(client, dereg)))[0].add(
            functools.partial(self.clear_function, path, globally, trailing_slash, session_id)
        )

    def clear_function(self, path: str, globally: bool, trailing_slash: bool, session_id: str):
        full_path = _RouteRegister._get_full_path(path, globally, trailing_slash, session_id)
        self._the_rules.named_rules.pop(full_path, None)

    def clear_all(self):
        self._the_rules.named_rules.clear()
        self._the_rules.rules.clear()


def st_route(path: str, globally: bool = False, trailing_slash: bool = True):
    if not isinstance(path, str) or not path:
        raise AttributeError('First argument must be a not empty path')

    def wrap(f: Callable):
        rr: _RouteRegister = _RouteRegister.instance()
        rr.regist_or_replace(path, globally, trailing_slash, f)

        setattr(f, 'clear', lambda: rr.clear_function(path, globally, trailing_slash, get_script_run_ctx().session_id))

        return f

    return wrap


setattr(st_route, 'clear', lambda: _RouteRegister.instance().clear_all())