#!/usr/bin/env python3
"""Real TCP/UDP + CLI subprocess + ctypes C AN3155 core + fake UART."""
import ctypes, hmac, importlib.util, json, os, pathlib, socket, struct, subprocess, tempfile, threading, time, zlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('romctl', ROOT/'tools/romctl.py')
assert spec is not None and spec.loader is not None
ctl=importlib.util.module_from_spec(spec); spec.loader.exec_module(ctl)
_cands=[pathlib.Path(os.environ.get('RMB_NATIVE_LIBRARY', ROOT/'out/native/librombridge.so')), ROOT/'build-rom/librombridge.so',ROOT/'build/librombridge.so']
_lib_path=next((p for p in _cands if p.exists()),None)
assert _lib_path is not None,'build librombridge.so first (cmake --build)'
lib=ctypes.CDLL(str(_lib_path))
lib.bridge_download.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_uint,ctypes.c_int]
lib.bridge_init_device.argtypes=[ctypes.c_int]
DEVICES=[
 {'name':'f407zg','msp':0x20020000,'ram_base':0x20004000,'ram_max':0x1c000,
  'flash_base':0x08000000,'flash_max':0x100000},
 {'name':'f103c8','msp':0x20005000,'ram_base':0x20000400,'ram_max':0x4c00,
  'flash_base':0x08000000,'flash_max':0x10000},
]
CURRENT={'name':DEVICES[0]['name'],'ram_max':DEVICES[0]['ram_max'],'flash_max':DEVICES[0]['flash_max']}
lib.bridge_init_device(0)
TOKEN='test-token-0123456789'
stop=threading.Event()
tcp=socket.socket();tcp.bind(('127.0.0.1',0));tcp.listen();tcp.settimeout(.1)
udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);udp.bind(('127.0.0.1',0));udp.settimeout(.1)
port=tcp.getsockname()[1];dport=udp.getsockname()[1]
announce={'service':ctl.SERVICE,'id':'native-c-core','version':'1','port':port}
listener=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);listener.bind(('',0));listener.settimeout(2)
broadcast_port=listener.getsockname()[1]

def reply(s,obj):
    b=(json.dumps(obj)+'\n').encode()
    for i in range(0,len(b),3):s.sendall(b[i:i+3])

def server():
    while not stop.is_set():
        try:s,_=tcp.accept()
        except socket.timeout:continue
        with s:
            s.settimeout(1)
            try:
                with s.makefile('rb') as f:
                    request=json.loads(f.readline(1025))
                    if not hmac.compare_digest(str(request.get('token','')),TOKEN):
                        reply(s,{'ok':False,'error':'auth'});continue
                    op=request['op']
                    if op=='status':reply(s,{'ok':True});continue
                    if op=='restart':rc=lib.bridge_restart()
                    elif op in ('flash','ram'):
                        size=request['size']
                        limit=CURRENT['ram_max'] if op=='ram' else CURRENT['flash_max']
                        if not 8<=size<=limit:
                            reply(s,{'ok':False,'error':'size'});continue
                        reply(s,{'ok':True,'ready':True})
                        data=f.read(size)
                        if len(data)!=size:continue
                        buf=ctypes.create_string_buffer(data)
                        rc=lib.bridge_download(buf,size,request['crc32'],op=='ram')
                    else:rc=-1
                    reply(s,{'ok':not rc,'error':rc})
            except (OSError,ValueError,KeyError):pass

def discovery():
    udp.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1)
    next_send=0
    while not stop.is_set():
        if time.monotonic()>=next_send:
            udp.sendto(json.dumps(announce).encode(),('127.255.255.255',broadcast_port));next_send=time.monotonic()+.2
        try:data,peer=udp.recvfrom(1024)
        except socket.timeout:continue
        if data==ctl.DISCOVER:udp.sendto(json.dumps(announce).encode(),peer)

threads=[threading.Thread(target=f,daemon=True) for f in (server,discovery)]
for t in threads:t.start()
def counts():return tuple(lib.bridge_count(i) for i in range(6))
def cli(op,path=None,success=True):
    args=['python3',str(ROOT/'tools/romctl.py'),op]
    if path:args.append(str(path))
    args+=['--host','127.0.0.1','--port',str(port),'--discovery-port',str(dport),'--timeout','2','--device',CURRENT['name']]
    p=subprocess.run(args,env={**os.environ,'STM32_ROM_TOKEN':TOKEN},capture_output=True,text=True)
    assert (p.returncode==0)==success,(args,p.stdout,p.stderr)
    print(op,p.returncode,p.stdout.strip() or p.stderr.strip())
try:
    before=counts()
    msg,_=listener.recvfrom(1024);assert json.loads(msg)['id']=='native-c-core'
    cli('discover');cli('status')
    try:ctl.control('127.0.0.1',port,'wrong','restart')
    except RuntimeError:pass
    else:raise AssertionError('authentication accepted')
    assert counts()==before,'discovery/status/auth changed target'
    cli('restart');assert lib.bridge_count(1)==1
    with tempfile.TemporaryDirectory(dir=str(_lib_path.parent)) as td:
        for index, dev in enumerate(DEVICES):
            CURRENT.update(name=dev['name'], ram_max=dev['ram_max'], flash_max=dev['flash_max'])
            lib.bridge_init_device(index)
            go_before = lib.bridge_count(2)
            for ram in (False, True):
                base = dev['ram_base'] if ram else dev['flash_base']
                data = struct.pack('<II', dev['msp'], base+9) + bytes(range(256))*8 + b'x'
                path = pathlib.Path(td)/f"{dev['name']}-{'ram' if ram else 'flash'}.bin"
                path.write_bytes(data)
                cli('ram' if ram else 'flash', path)
                result = ctl.control('127.0.0.1', port, TOKEN, 'ram' if ram else 'flash', data, fragment=1)
                assert result['ok'], (dev['name'], ram, result)
            # CLI subprocess + direct ctl.control each run one RAM image
            assert lib.bridge_count(2) == go_before+2, dev['name']
            before = counts()
            try: ctl.control('127.0.0.1', port, TOKEN, 'flash', b'bad image')
            except RuntimeError: pass
            else: raise AssertionError('bad image accepted')
            assert counts() == before, 'invalid image touched target'
            s = socket.create_connection(('127.0.0.1', port))
            s.sendall((json.dumps({'token': TOKEN, 'op': 'flash', 'size': 100, 'crc32': 0})+'\n').encode())
            s.recv(1024); s.sendall(b'partial'); s.close(); time.sleep(.2)
            assert counts() == before, 'disconnect touched target'
            lib.bridge_corrupt(1)
            data = struct.pack('<II', dev['msp'], dev['ram_base']+9) + b'abcdefgh'
            try: ctl.control('127.0.0.1', port, TOKEN, 'ram', data)
            except RuntimeError: pass
            else: raise AssertionError('readback corruption accepted')
            assert lib.bridge_count(2) == go_before+2, 'corrupt image executed'
            lib.bridge_corrupt(0)

    # The artifacts we actually ship must survive the loader path end to end.
    real = ROOT/'app_cubemx_f103/build'
    built = [real/f'{v}-{t}/esp32c3-stm32f103-loader.bin'
             for v in ('blink','doubleblink') for t in ('flash','ram')]
    built = [b for b in built if b.exists()]
    if len(built) == 4:
        lib.bridge_init_device(1)
        CURRENT.update(name='f103c8', ram_max=0x4c00, flash_max=0x10000)
        go_before = lib.bridge_count(2)
        for path in built:
            target = 'ram' if '-ram' in path.parent.name else 'flash'
            result = ctl.control('127.0.0.1', port, TOKEN, target, path.read_bytes())
            assert result['ok'], (path.name, result)
        assert lib.bridge_count(2) == go_before+2, 'real images: expected one Go per RAM image'
        print('PASS 4 shipped F103C8 images (blink/doubleblink x flash/ram) loaded via the ROM path')
    else:
        print('SKIP shipped-image check (build app_cubemx_f103 first)')

    assert lib.bridge_count(5)==0
    print('PASS TCP/UDP + CLI + C core for', [d['name'] for d in DEVICES], ': broadcast, discovery, auth, restart, Flash/RAM, byte-fragment I/O, invalid/partial staging, corruption; counts=',counts())
finally:
    stop.set()
    for t in threads:t.join(2)
    tcp.close();udp.close();listener.close()
