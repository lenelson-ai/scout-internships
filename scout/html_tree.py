"""Small read-only HTML tree for provider markup; no scripts or external dependencies."""
from html.parser import HTMLParser
class Node:
 def __init__(self,tag='',attrs=(),parent=None):self.tag=tag;self.attrs=dict(attrs);self.parent=parent;self.children=[];self.parts=[]
 def text(self):return ' '.join(' '.join(x.text() if isinstance(x,Node) else x for x in self.parts).split())
 def all(self,tag=None,cls=None):
  out=[]
  for x in self.children:
   if (not tag or x.tag==tag) and (not cls or cls in x.attrs.get('class','').split()):out.append(x)
   out.extend(x.all(tag,cls))
  return out
class Tree(HTMLParser):
 VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
 def __init__(self,body):
  super().__init__(convert_charrefs=True);self.root=Node();self.current=self.root;self.feed(body);self.close()
 def handle_starttag(self,tag,attrs):
  n=Node(tag,attrs,self.current);self.current.children.append(n);self.current.parts.append(n)
  if tag not in self.VOID:self.current=n
 def handle_startendtag(self,tag,attrs):self.handle_starttag(tag,attrs);self.handle_endtag(tag)
 def handle_endtag(self,tag):
  n=self.current
  while n.parent and n.tag!=tag:n=n.parent
  if n.parent:self.current=n.parent
 def handle_data(self,data):
  if self.current.tag not in ('script','style'):self.current.parts.append(data)
