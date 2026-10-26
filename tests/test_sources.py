import io
import socket
import unittest
from unittest.mock import patch
from qq_character.sources import read_source


SOURCE={'id':'test','title':'Test','url':'https://example.org/article'}


class Response(io.BytesIO):
    status=200
    def getheader(self,name,default=''):return 'text/html; charset=utf-8' if name=='content-type' else default


class SourceTests(unittest.TestCase):
    def test_private_or_mixed_dns_is_rejected_before_request(self):
        for addresses in [[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))],
                          [(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443)),(socket.AF_INET,socket.SOCK_STREAM,6,'',('10.0.0.1',443))]]:
            with patch('qq_character.sources.socket.getaddrinfo',return_value=addresses),patch('http.client.HTTPSConnection.request') as request:
                with self.assertRaises(ValueError):read_source(SOURCE)
                request.assert_not_called()
    def test_credentials_ports_and_non_https_rejected(self):
        for url in ('http://example.org','https://a:b@example.org','https://example.org:8080'):
            with self.assertRaises(ValueError):read_source({**SOURCE,'url':url})
    def test_redirect_is_not_followed(self):
        response=Response();response.status=302
        with patch('qq_character.sources.socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]),patch('http.client.HTTPSConnection.request'),patch('http.client.HTTPSConnection.getresponse',return_value=response):
            with self.assertRaisesRegex(ValueError,'redirects'):read_source(SOURCE)
    def test_main_content_hash_and_size_cap(self):
        raw=b'<html><nav>noise</nav><main><p>Observed fact.</p><script>ignore me</script></main></html>'
        with patch('qq_character.sources.socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]),patch('http.client.HTTPSConnection.request'),patch('http.client.HTTPSConnection.getresponse',return_value=Response(raw)):
            result=read_source(SOURCE)
            self.assertEqual(result['text'],'Observed fact.')
            self.assertEqual(len(result['sha256']),64)
            self.assertFalse(result['truncated'])


if __name__=='__main__':unittest.main()
