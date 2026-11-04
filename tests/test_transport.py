import unittest,time,urllib.request,urllib.error,http.client
from unittest.mock import patch,MagicMock
from scout.sources.transport import Session
class TransportTests(unittest.TestCase):
 def request(self):
  r=urllib.request.Request('https://jobs.example.org/search');r.timeout=10;return r
 def test_tls_connection_reused_and_closed(self):
  conn=MagicMock();conn.sock=None
  with patch('scout.sources.transport.http.client.HTTPSConnection',return_value=conn) as ctor:
   s=Session(time.monotonic()+30);ctx=object()
   s.https(self.request(),ctx);s.https(self.request(),ctx)
   self.assertEqual(ctor.call_count,1);self.assertEqual(conn.request.call_count,2);s.close();conn.close.assert_called_once()
 def test_proxy_credentials_never_reach_origin_even_on_reuse(self):
  conn=MagicMock();conn.sock=None
  with patch('scout.sources.transport.http.client.HTTPSConnection',return_value=conn):
   s=Session(time.monotonic()+30);ctx=object()
   for _ in range(2):
    r=self.request();r._tunnel_host='jobs.example.org';r.add_header('Proxy-Authorization','Basic test');s.https(r,ctx)
   conn.set_tunnel.assert_called_once_with('jobs.example.org',headers={'Proxy-Authorization':'Basic test'})
   for call in conn.request.call_args_list:self.assertNotIn('Proxy-Authorization',call.args[3])
 def test_only_stale_reused_connection_retried(self):
  a=MagicMock();b=MagicMock();a.sock=b.sock=None
  a.request.side_effect=[None,http.client.RemoteDisconnected('stale')]
  with patch('scout.sources.transport.http.client.HTTPSConnection',side_effect=[a,b]) as ctor:
   s=Session(time.monotonic()+30);ctx=object();s.https(self.request(),ctx);s.https(self.request(),ctx);self.assertEqual(ctor.call_count,2)
 def test_deadline_prevents_dispatch(self):
  with self.assertRaises(urllib.error.URLError):Session(time.monotonic()-1).https(self.request(),object())
