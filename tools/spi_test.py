#!/usr/bin/env python3
"""Run authenticated ESP-master/STM-slave SPI diagnostics on the trusted LAN."""
import argparse,json,os,socket,sys
from pathlib import Path

def run(host,token,count=10,hz=100000,timeout=60):
    if not 1<=count<=100 or not 100000<=hz<=1000000:
        raise ValueError('count must be 1..100; hz must be 100000..1000000')
    header=dict(token=token,op='spi-test',count=count,hz=hz)
    with socket.create_connection((host,3333),timeout=5) as sock:
        sock.settimeout(timeout)
        sock.sendall(json.dumps(header,separators=(',',':')).encode()+b'\n')
        with sock.makefile('rb') as stream:
            line=stream.readline(16385)
        if not line.endswith(b'\n') or len(line)>16384:
            raise RuntimeError('missing or overlong SPI result')
        result=json.loads(line)
        if not isinstance(result,dict):raise RuntimeError('invalid SPI result')
        return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host',required=True);p.add_argument('--count',type=int,default=10)
    p.add_argument('--hz',type=int,default=100000);p.add_argument('--token-file',type=Path)
    a=p.parse_args()
    try:
        token=a.token_file.read_text().strip() if a.token_file else os.environ.get('STM32_ROM_TOKEN')
        if not token:token=Path(__file__).with_name('token.txt').read_text().strip()
        if not token:raise ValueError('empty token forbidden')
        result=run(a.host,token,a.count,a.hz);print(json.dumps(result,ensure_ascii=True))
        return 0 if result.get('ok') else 1
    except (OSError,ValueError,RuntimeError) as exc:
        print(str(exc),file=sys.stderr);return 1
if __name__=='__main__':raise SystemExit(main())
