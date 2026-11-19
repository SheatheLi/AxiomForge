"""The local cancellation experiment must run against its own TCP server."""
import asyncio
import importlib.util
from pathlib import Path


def test_local_repro_ignores_ambient_proxy(monkeypatch):
    for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
        monkeypatch.setenv(name, 'unsupported-proxy-scheme://invalid.local:1')
    source=Path(__file__).resolve().parents[2]/'benchmarks/resource_protocol/httpx_double_cancel_local.py'
    spec=importlib.util.spec_from_file_location('isolated_loopback_repro',source)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result=asyncio.run(module.run_variant(1,repetitions=1))
    assert result is not None
