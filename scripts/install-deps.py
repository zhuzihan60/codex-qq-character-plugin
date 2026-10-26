"""Fetch exact PyPI release wheels and verify their published SHA256, then install.

Avoids scanning the entire package release index on hosts with slow pip resolution.
Does not alter system Python. Run with this project's .venv Python.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

PACKAGES={'websockets':'15.0.1','tzdata':'2025.2','setuptools':'80.9.0','wheel':'0.45.1'}
root=Path(__file__).resolve().parents[1]
target=root/'research'/'wheels';target.mkdir(parents=True,exist_ok=True)
paths=[]
for name,version in PACKAGES.items():
    with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{version}/json',timeout=20) as response:
        meta=json.load(response)
    # The universal pure-Python wheels work on both Windows and Linux.
    wheel=next(w for w in meta['urls'] if w['filename'].endswith('py3-none-any.whl'))
    with urllib.request.urlopen(wheel['url'],timeout=30) as response:raw=response.read()
    if hashlib.sha256(raw).hexdigest()!=wheel['digests']['sha256']:raise RuntimeError('wheel digest mismatch')
    path=target/wheel['filename'];path.write_bytes(raw);paths.append(str(path))
    print('verified',wheel['filename'],flush=True)
subprocess.run([sys.executable,'-m','pip','--disable-pip-version-check','install','--no-index','--no-deps',*paths],check=True)
subprocess.run([sys.executable,'-m','pip','--disable-pip-version-check','install','--no-index','--no-deps','--no-build-isolation','-e',str(root)],check=True)
