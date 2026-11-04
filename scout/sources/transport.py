"""Per-crawl HTTPS reuse through urllib's normal proxy/redirect/TLS handling."""
import http.client,urllib.request,urllib.error,ssl,os,time
from pathlib import Path
from contextvars import ContextVar
from functools import lru_cache
SESSION=ContextVar('scout_public_http_session',default=None)
@lru_cache(maxsize=4)
def _context(cert_env,system_stamp):
 path='/etc/ssl/cert.pem'
 return ssl.create_default_context(cafile=path if not cert_env and system_stamp else None)
def tls_context():
 path=Path('/etc/ssl/cert.pem');return _context(os.environ.get('SSL_CERT_FILE'),path.stat().st_mtime_ns if path.exists() else None)
class Session:
 def __init__(self,deadline):self.deadline=deadline;self.connections={};self.openers={}
 def close(self):
  for c in self.connections.values():c.close()
  self.connections.clear()
 def open(self,request,context,timeout):
  context=context or tls_context()
  if context not in self.openers:self.openers[context]=urllib.request.build_opener(Handler(self,context))
  return self.openers[context].open(request,timeout=timeout)
 def https(self,req,context):
  headers=dict(req.unredirected_hdrs);headers.update({k:v for k,v in req.headers.items() if k not in headers});headers={k.title():v for k,v in headers.items()};headers['Connection']='keep-alive'
  tunnel=getattr(req,'_tunnel_host',None);proxy_auth=headers.pop('Proxy-Authorization',None) if tunnel else None;key=(req.host,tunnel,context,proxy_auth)
  for attempt in range(2):
   remaining=self.deadline-time.monotonic()
   if remaining<=0:raise urllib.error.URLError(TimeoutError('Crawl deadline exhausted'))
   timeout=min(req.timeout,remaining);reused=key in self.connections
   if not reused:
    c=http.client.HTTPSConnection(req.host,timeout=timeout,context=context)
    if tunnel:
     tunnel_headers={}
     if proxy_auth:tunnel_headers['Proxy-Authorization']=proxy_auth
     c.set_tunnel(tunnel,headers=tunnel_headers)
    self.connections[key]=c
   c=self.connections[key];c.timeout=timeout
   if c.sock:c.sock.settimeout(timeout)
   try:
    c.request(req.get_method(),req.selector,req.data,headers,encode_chunked=req.has_header('Transfer-encoding'));r=c.getresponse();r.url=req.get_full_url();r.msg=r.reason;return r
   except (OSError,http.client.HTTPException) as e:
    c.close();self.connections.pop(key,None)
    # Searches are read-only, including provider JSON search POSTs. Only one
    # stale keepalive retry; HTTP denials never enter this branch.
    if attempt==0 and reused and isinstance(e,(http.client.RemoteDisconnected,BrokenPipeError,ConnectionResetError)):continue
    raise urllib.error.URLError(e) from e
class Handler(urllib.request.HTTPSHandler):
 def __init__(self,pool,context):super().__init__(context=context);self.pool=pool
 def https_open(self,req):return self.pool.https(req,self._context)
def open_url(request,context=None,timeout=30):
 pool=SESSION.get()
 return pool.open(request,context,timeout) if pool else urllib.request.urlopen(request,context=context,timeout=timeout)
