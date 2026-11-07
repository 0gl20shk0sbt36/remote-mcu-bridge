#!/usr/bin/env python3
"""ESP-IDF build driver. No flash/monitor/hardware commands are exposed."""
import argparse,json,os,re,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('command',choices=['configure','build'])
for name in ('root','backend','board','build-dir'): p.add_argument('--'+name,type=Path,required=True)
p.add_argument('--profile',choices=['safe','compile-enabled'],default='safe');p.add_argument('--user-config',type=Path)
a=p.parse_args()
# The driver is also callable directly: apply the same dependency-free contract
# validation before invoking the SDK or creating build state.
sys.path.insert(0, str(a.root.resolve()/'tools'))
from rmb import validate, compatible
try:
    backend=json.loads(a.backend.read_text()); board=json.loads(a.board.read_text())
    validate(backend,a.root,a.backend,'backends')
    validate(board,a.root,a.board,'boards')
    compatible(backend,board)
    if (backend['id']!='esp-idf' or board['id']!='esp32c3-stm32f103'
            or board['target']!='esp32c3'
            or board.get('target_device')!='stm32f103c8'
            or board['pins']!={'tx':3,'rx':4,'boot0':5,'nrst':6,'boot1':-1,'spi_sclk':7,'spi_mosi':1,'spi_miso':0,'spi_cs':10}):
        raise ValueError('ESP-IDF driver supports only the fixed esp32c3-stm32f103 board with fixed documented wiring; configure UART/BOOT/reset through matching Kconfig defaults')
    if a.build_dir.resolve()==a.root.resolve() or a.root.resolve().is_relative_to(a.build_dir.resolve()):
        raise ValueError('unsafe build directory')
except (ValueError,OSError) as exc:
    p.error(str(exc))
idf=Path(os.environ.get('IDF_PATH','__missing_idf__')); tool=idf/'tools/idf.py'
if not tool.is_file(): p.error('ESP-IDF 5.5.5 required: set IDF_PATH and the installed tool/Python environment')
version=subprocess.check_output([sys.executable,str(tool),'--version'],text=True).strip()
if not re.search(r'v?5\.5\.5(?:\s|$)',version): p.error('Expected ESP-IDF 5.5.5, got '+version)
defaults=[str(a.root/x) for x in board['defaults'] if x!='config/ci-disabled.defaults']
defaults.append(str(a.root/('config/ci-enabled.defaults' if a.profile=='compile-enabled' else 'config/ci-disabled.defaults')))
if a.user_config:
    if not a.user_config.is_file(): p.error('user configuration does not exist')
    defaults.append(str(a.user_config.resolve()))
a.build_dir.mkdir(parents=True,exist_ok=True)
config=a.build_dir/'sdkconfig'
contract={'backend':backend['id'],'board':board['id'],'profile':a.profile,'defaults':defaults}
state=a.build_dir/'rmb-build.json'
if state.exists() and json.loads(state.read_text())!=contract: p.error('build directory belongs to different configuration; use a new directory')
state.write_text(json.dumps(contract,indent=2)+'\n')
command=[sys.executable,str(tool),'-C',str(a.root/backend['implementation']),'-B',str(a.build_dir),'-DIDF_TARGET='+board['target'],'-DSDKCONFIG='+str(config),'-DSDKCONFIG_DEFAULTS='+';'.join(defaults),'reconfigure' if a.command=='configure' else 'build']
print('SDK:',version,flush=True)
rc=subprocess.call(command)
if rc == 0:
    values={}
    for line in config.read_text().splitlines():
        if line.startswith('CONFIG_') and '=' in line:
            key,value=line.split('=',1); values[key]=value
    enabled=values.get('CONFIG_ROM_SERVICE_ENABLE')=='y'
    if enabled != (a.profile=='compile-enabled'):
        p.error('generated sdkconfig service enable does not match selected profile')
    if enabled:
        for key,pin in [('TX','tx'),('RX','rx'),('BOOT0','boot0'),('NRST','nrst'),('BOOT1','boot1')]:
            if values.get('CONFIG_ROM_SERVICE_'+key)!=str(board['pins'][pin]):
                p.error('generated sdkconfig wiring differs from fixed board: '+key)
    print('Generated sdkconfig profile and fixed-board wiring validated.',flush=True)
sys.exit(rc)
