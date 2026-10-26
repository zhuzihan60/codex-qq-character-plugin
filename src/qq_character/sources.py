"""Only operator-selected public HTTPS sources, fetched outside Codex's sandbox."""
from __future__ import annotations
import hashlib
import html
import ipaddress
import re
import socket
import ssl
import http.client
import time
from urllib.parse import urlsplit


def read_source(source, timeout=12, max_bytes=131072):
    url=source['url']
    p=urlsplit(url)
    if p.scheme!='https' or p.username or p.password or p.port not in (None,443) or not p.hostname or p.fragment:
        raise ValueError('source must be public HTTPS without credentials')
    addresses=socket.getaddrinfo(p.hostname,443,type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('private, loopback and reserved source addresses are forbidden')
    # Pin the validated address while retaining the original TLS hostname.
    class PinnedHTTPS(http.client.HTTPSConnection):
        def connect(self):
            self.sock=socket.create_connection((addresses[0][4][0],443),self.timeout)
            self.sock=self._context.wrap_socket(self.sock,server_hostname=p.hostname)
    conn=PinnedHTTPS(p.hostname,timeout=timeout,context=ssl.create_default_context())
    try:
        conn.request('GET',p.path+('?' + p.query if p.query else ''),headers={'User-Agent':'QQCharacter/0.1 (bounded reading)','Accept':'text/html,text/plain,application/json','Accept-Encoding':'identity'})
        response=conn.getresponse()
        if response.status!=200:
            raise ValueError(f'source HTTP {response.status}; redirects require explicit source correction')
        kind=response.getheader('content-type','').lower()
        if not any(x in kind for x in ('text/','application/json','application/xml')):
            raise ValueError('source is not text')
        deadline=time.monotonic()+timeout
        chunks=[];size=0
        while size<=max_bytes:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('source read deadline')
            if conn.sock:conn.sock.settimeout(remaining)
            chunk=response.read1(min(8192,max_bytes+1-size))
            if not chunk:break
            chunks.append(chunk);size+=len(chunk)
        raw=b''.join(chunks)
        if len(raw)>max_bytes:raw=raw[:max_bytes]
    finally:
        conn.close()
    text=raw.decode('utf-8',errors='replace')
    article=re.search(r'<main\b[^>]*>(.*?)</main>',text,flags=re.S|re.I)
    if article:text=article.group(1)
    text=re.sub(r'<(script|style|nav|header|footer)\b[^>]*>.*?</\1>',' ',text,flags=re.S|re.I)
    text=html.unescape(re.sub(r'<[^>]+>',' ',text))
    text=re.sub(r'\s+',' ',text).strip()[:6500]
    return {'title':source['title'],'url':url,'text':text,'sha256':hashlib.sha256(raw).hexdigest(),'truncated':len(raw)>=max_bytes}
