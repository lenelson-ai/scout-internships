"""Atomic WAL-aware scanner checkpoint, including committed work after an interrupted run."""
import argparse,gzip,os,sqlite3,tempfile
from pathlib import Path

def checkpoint(source,output):
 source=Path(source).resolve();output=Path(output)
 if not source.is_file() or not source.stat().st_size:raise ValueError('Missing scanner state; refusing to create an empty replacement')
 output.parent.mkdir(parents=True,exist_ok=True);fd,name=tempfile.mkstemp(prefix='scout-checkpoint-',suffix='.sqlite3',dir=output.parent);os.close(fd);temp=Path(name)
 try:
  src=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True);dst=sqlite3.connect(temp)
  try:
   src.backup(dst)
   if dst.execute('pragma integrity_check').fetchone()[0]!='ok' or dst.execute('pragma foreign_key_check').fetchall():raise ValueError('Scanner checkpoint failed integrity validation')
   if not dst.execute('select count(*) from sources').fetchone()[0]:raise ValueError('Empty scanner source registry; checkpoint withheld')
   dst.execute('pragma wal_checkpoint(TRUNCATE)')
  finally:dst.close();src.close()
  zipped=output.with_suffix(output.suffix+'.tmp');zipped.write_bytes(gzip.compress(temp.read_bytes(),mtime=0));os.replace(zipped,output)
 finally:
  temp.unlink(missing_ok=True)
  for suffix in ('-wal','-shm'):Path(str(temp)+suffix).unlink(missing_ok=True)
 return output

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--output',required=True);a=p.parse_args();checkpoint(a.db,a.output);print('Validated atomic scanner checkpoint saved')
