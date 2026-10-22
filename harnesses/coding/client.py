"""Local HTTP transport with a real wall-clock deadline and recoverable partials."""
from contextlib import contextmanager
import json
from pathlib import Path
import signal
import socket
import time
from urllib.parse import urlsplit
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

import bootstrap
from fieldkit_runtime.adapters import models
from fieldkit_runtime.stream import ChatStream


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


@contextmanager
def deadline_alarm(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError('Request wall-clock budget exhausted')
    def expired(*_):
        raise TimeoutError('Request wall-clock budget exhausted')
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise RuntimeError('Nested process timers are unsupported')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class ObservedStream(ChatStream):
    def __init__(self, raw_path, status, observer):
        super().__init__(raw_path)
        self.status, self.observer, self.updated = status, observer, 0.0
        self.native_chunks, self.native_final = [], None

    def event(self, event):
        super().event(event)
        native = event.get('__verbose')
        if isinstance(native, dict) and isinstance(native.get('content'), str):
            if native.get('stop'):
                self.native_final = native['content']
            else:
                self.native_chunks.append(native['content'])
        if self.observer:
            self.observer()
        if time.monotonic() - self.updated >= 3:
            self.updated = time.monotonic()
            save(self.status, {'elapsed_seconds': self.clock() - self.started,
                'reasoning_characters': sum(map(len, self.reasoning)),
                'answer_characters': sum(map(len, self.content)),
                'tool_argument_characters': sum(len(c['function']['arguments']) for c in self.calls.values()),
                'timings': self.timings})


class Client:
    def __init__(self, base_url, model, context, sampling, *, observer=None, native_root=None,
                 model_adapter="openai/v1", output_tokens=None):
        parts = urlsplit(base_url)
        if (parts.scheme != 'http' or parts.hostname not in ('127.0.0.1', 'localhost', '::1')
                or parts.username or parts.password or parts.query or parts.fragment
                or parts.path.rstrip('/') != '/v1'):
            raise ValueError('Use the local /v1 endpoint supplied by the supervisor')
        self.base, self.model = base_url.rstrip('/'), model
        self.native = native_root.rstrip('/') if native_root else self.base[:-3]
        self.context, self.sampling, self.observer = context, dict(sampling), observer
        self.output_tokens = output_tokens
        self.adapter = models.resolve(model_adapter)
        self.opener = build_opener(ProxyHandler({}))

    def json(self, path, payload, deadline):
        request = Request(self.native + path,
                          data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode(),
                          headers={'Content-Type': 'application/json'})
        with deadline_alarm(deadline):
            with self.opener.open(request, timeout=max(.1, deadline - time.monotonic())) as response:
                return json.load(response)

    def request(self, directory, body, deadline):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        if self.observer:
            self.observer()
        if self.output_tokens:
            # No token preflight: the engine lacks template/tokenizer routes; the caller fixed the allowance.
            tokens, allowance = None, self.output_tokens
        else:
            # One outgoing message representation owns both token budgeting and inference.
            try:
                rendered = self.json('/apply-template', {**body, 'add_generation_prompt': True}, deadline)
                rendered = rendered['prompt'] if isinstance(rendered, dict) else rendered
                encoded = self.json('/tokenize', {'content': rendered, 'add_special': False,
                                                 'parse_special': True}, deadline)
            except HTTPError as error:
                raise RuntimeError(f'Engine has no native template/tokenizer route ({error}); '
                                   'pass --output-tokens to use a fixed output allowance') from error
            tokens = len(encoded['tokens'] if isinstance(encoded, dict) else encoded)
            allowance = self.context - tokens - 256
            if allowance <= 0:
                raise TimeoutError('Complete preserved history exhausted the context window')
            (directory / 'rendered.txt').write_text(rendered)
        payload = {**body, **self.sampling, 'model': self.model, 'max_tokens': min(allowance, body.get('max_tokens', allowance)),
                   'stream': True, 'stream_options': {'include_usage': True},
                   'cache_prompt': True, 'return_progress': True, 'timings_per_token': True,
                   'verbose': True, 'return_tokens': True}
        save(directory / 'request.json', payload)
        save(directory / 'budget.json', {'input_tokens': tokens, 'output_allowance': allowance,
             'context_tokens': self.context, 'framing_reserve': None if tokens is None else 256,
             'token_preflight': tokens is not None})
        stream = ObservedStream(directory / 'response.sse', directory / 'status.json', self.observer)
        started, result = time.monotonic(), None
        try:
            request = Request(self.base + '/chat/completions', data=json.dumps(payload, ensure_ascii=False).encode(),
                              headers={'Content-Type': 'application/json'})
            with deadline_alarm(deadline):
                with self.opener.open(request, timeout=max(.1, deadline - time.monotonic())) as response:
                    result = self.adapter.normalize(stream(response, started), payload)
            if self.observer:
                self.observer()
            return result
        except BaseException as error:
            result = self.adapter.normalize(stream.snapshot(), payload)
            result['error'] = type(error).__name__ + ': ' + str(error)
            if isinstance(error, socket.timeout):
                result['error'] = 'TimeoutError: Request socket reached its wall-clock deadline'
                raise TimeoutError('Request socket reached its wall-clock deadline') from error
            raise
        finally:
            result = result or self.adapter.normalize(stream.snapshot(), payload)
            result['elapsed_seconds'] = time.monotonic() - started
            result['input_tokens_preflight'] = tokens
            result['output_allowance'] = allowance
            native_text = stream.native_final or ''.join(stream.native_chunks)
            result['native_text_observed'] = bool(stream.native_chunks) or stream.native_final is not None
            if result['native_text_observed']:
                (directory / 'native-output.txt').write_text(native_text)
            save(directory / 'response.json', result)
