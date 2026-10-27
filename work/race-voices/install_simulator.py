"""Install the official portable release and connect local speech in one task."""
import argparse, configparser, hashlib, json, os, stat, tempfile, zipfile, shutil
from datetime import datetime
from pathlib import Path
import requests, psutil
from runtime_settings import project_root, simulator_root, documents
from atomic_io import write_json
from setup_simulator import main as connect_speech

MANIFEST = 'https://raw.githubusercontent.com/SeriousOldMan/Simulator-Controller/main/VERSION'
ESSENTIAL = ('Simulator Startup.exe', 'Simulator Controller.exe', 'Simulator Setup.exe',
             'Race Engineer.exe', 'Race Spotter.exe', 'Race Strategist.exe')

def ini(text):
    result = configparser.ConfigParser(interpolation=None, strict=False)
    result.optionxform = str; result.read_string(text); return result

def https_urls(value):
    urls = [x.strip() for x in value.split(';') if x.strip().startswith('https://')]
    if not urls: raise ValueError('官方清单中没有可用的 HTTPS 下载地址。')
    return urls

def safe_extract(archive, destination):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as z:
        total = 0
        for item in z.infolist():
            name = item.filename.replace('\\', '/')
            path = (destination / name).resolve()
            if not path.is_relative_to(destination) or ':' in name or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('安装压缩包包含无效路径。')
            total += item.file_size
            if total > 4 * 1024**3: raise ValueError('安装压缩包展开后过大。')
        z.extractall(destination)

def download_archive(urls, destination):
    errors = []
    for url in urls:
        try:
            print('正在下载：' + url.split('?')[0], flush=True)
            size = 0; digest = hashlib.sha256()
            with requests.get(url, stream=True, timeout=(15, 90)) as response:
                response.raise_for_status()
                with Path(destination).open('wb') as f:
                    for block in response.iter_content(1024 * 1024):
                        size += len(block)
                        if size > 2 * 1024**3: raise ValueError('下载包超过大小限制。')
                        f.write(block); digest.update(block)
            with zipfile.ZipFile(destination) as z:
                if z.testzip(): raise ValueError('下载文件校验失败。')
            print('下载并校验完成：' + str(round(size / 1024**2, 1)) + ' MB', flush=True)
            return dict(url=url, sha256=digest.hexdigest(), bytes=size)
        except (requests.RequestException, OSError, ValueError, zipfile.BadZipFile) as error:
            errors.append(str(error)); print('该下载源不可用，尝试其他官方镜像。', flush=True)
    raise RuntimeError('所有下载源均失败：' + '; '.join(errors))

def ensure_stopped(root):
    root = Path(root).resolve()
    for p in psutil.process_iter(['exe']):
        try:
            if p.info['exe'] and Path(p.info['exe']).resolve().is_relative_to(root):
                raise RuntimeError('请先关闭 Simulator Controller 各模块，再重新执行一键安装并接入。')
        except (psutil.Error, OSError): continue

def validate_install(root):
    for filename in ESSENTIAL:
        with (Path(root) / 'Binaries' / filename).open('rb') as f:
            if f.read(2) != b'MZ': raise ValueError('组件文件无效：' + filename)

def install_portable(target, manifest_text=None):
    target = Path(target)
    if target.exists(): raise RuntimeError('目标目录已存在，不会覆盖：' + str(target))
    if manifest_text is None:
        response = requests.get(MANIFEST, timeout=(15, 60)); response.raise_for_status()
        manifest_text = response.content.decode('utf-8-sig')
    manifest = ini(manifest_text); version = manifest['Release']['Version']
    if not version.endswith('-release'): raise ValueError('不是官方正式发布版本。')
    target.parent.mkdir(parents=True, exist_ok=True); records = []
    with tempfile.TemporaryDirectory(prefix='sc-proxy-', dir=target.parent) as temporary:
        temporary = Path(temporary); archive = temporary / 'release.zip'; extracted = temporary / 'package'
        records.append(download_archive(https_urls(manifest['Release']['Download']), archive))
        safe_extract(archive, extracted)
        roots = [p.parent.parent for p in extracted.rglob('Simulator Startup.exe') if p.parent.name == 'Binaries']
        if len(roots) != 1: raise ValueError('官方安装包目录结构无法识别。')
        root = roots[0]
        for component in manifest['Release'].get('Components', '').split(','):
            if not component.strip(): continue
            name, component_version = component.strip().split('->', 1); prefix = name + '.' + component_version
            info = manifest['Components']; destination = (root / info[prefix + '.Path']).resolve()
            if not destination.is_relative_to(root.resolve()): raise ValueError('组件目标路径无效。')
            archive = temporary / (name + '.zip'); print('安装组件：' + name, flush=True)
            records.append(download_archive(https_urls(info[prefix + '.Download']), archive))
            safe_extract(archive, destination)
            for content in info[prefix + '.Content'].split(','):
                if not (destination / content.strip()).exists(): raise ValueError('组件解压不完整：' + name)
        validate_install(root)
        (root / 'chinese-assistant-install.json').write_text(json.dumps(dict(version=version, manifest=MANIFEST, downloads=records), ensure_ascii=False, indent=2), encoding='utf-8')
        root.rename(target)
    return version

def prepare_config(root, docs):
    """Create only missing files; existing personal configuration is preserved."""
    folder = Path(docs) / 'Simulator Controller/Config'; folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'Simulator Configuration.ini'
    if not target.exists():
        raw = (Path(root) / 'Config/Simulator Configuration.ini').read_bytes()
        config = ini(raw.decode('utf-16' if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8-sig'))
        for section in ('Configuration', 'Plugins', 'iRacing', 'Voice Control'):
            if not config.has_section(section): config.add_section(section)
        config['Configuration'].update({'Language': 'zh', 'Simulators': 'iRacing', 'Start With Windows': 'false'})
        config['Plugins'].update({'System': 'true||', 'IRC': 'true|iRacing|togglePitstopFuelMFD: {F4}; togglePitstopTyreMFD: {F5}'})
        config['iRacing'].update({'Window Title': 'ahk_exe iRacingSim64DX11.exe'})
        config['Voice Control'].update({'Language': 'zh', 'Listener': 'false', 'Speaker': 'true', 'SpeakerVolume': '100', 'Speaker.NoiseVolume': '0', 'Speaker.ClickVolume': '0'})
        with target.open('x', encoding='utf-8-sig') as f: config.write(f, space_around_delimiters=False)
    raw = target.read_bytes(); encoding = 'utf-16' if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8-sig'
    text = raw.decode(encoding); after = enable_iracing(text)
    if after != text:
        shutil.copy2(target, target.with_name(target.name + '.before-proxy-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.bak'))
        temp = target.with_suffix('.proxy.tmp'); temp.write_bytes(after.encode(encoding)); temp.replace(target)
    installation = folder / 'Simulator Controller.install'
    if (Path(root) / 'chinese-assistant-install.json').is_file() and not installation.exists():
        location = str(Path(root).resolve()).replace('\\', '\\\\').replace('=', '\\=')
        user = str(Path(docs) / 'Simulator Controller').replace('\\', '\\\\').replace('=', '\\=')
        installation.write_text('[Install]\nType=Portable\nLocation=' + location + '\nUser=' + user + '\n[Updates]\nAutomatic=true\nVerbose=false\n[Shortcuts]\nDesktop=false\nStartMenu=false\n', encoding='utf-8-sig')
    from simulator_identity import ensure_identity
    ensure_identity(folder.parent)
    return target

def enable_iracing(text):
    config = ini(text)
    simulators = config.get('Configuration', 'Simulators', fallback='').split('|')
    if 'iRacing' not in simulators: simulators.append('iRacing')
    irc = config.get('Plugins', 'IRC', fallback='true|iRacing|togglePitstopFuelMFD: {F4}; togglePitstopTyreMFD: {F5}')
    irc = 'true|' + irc.split('|', 1)[1] if '|' in irc else 'true|iRacing|'
    updates = {'Configuration': {'Language': 'zh', 'Simulators': '|'.join(x for x in simulators if x)},
               'Plugins': {'IRC': irc, 'System': 'true||'}}
    section = ''; result = []; seen_sections = set(); seen_keys = set()
    def append_missing():
        if section in updates:
            result.extend(k + '=' + v for k, v in updates[section].items() if k not in seen_keys)
    for line in text.splitlines():
        if line.startswith('['):
            append_missing(); section = line.strip('[]'); seen_sections.add(section); seen_keys = set()
        if section in updates and '=' in line:
            key = line.split('=', 1)[0].strip()
            if key in updates[section]: line = key + '=' + updates[section][key]; seen_keys.add(key)
        result.append(line)
    append_missing()
    for section, fields in updates.items():
        if section not in seen_sections: result.extend(['[' + section + ']', *(k + '=' + v for k, v in fields.items())])
    return '\n'.join(result) + '\n'

def save_root(root):
    path = project_root() / 'config/local.json'
    config = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    config['simulator_controller'] = str(root)
    write_json(path, config, '.proxy.tmp', ensure_ascii=False, indent=2)

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--check', action='store_true'); args = parser.parse_args()
    root = simulator_root(); docs = documents()
    if args.check:
        print(json.dumps(dict(root=str(root), installed=(root / 'Binaries/Simulator Startup.exe').is_file(), configuration=(docs / 'Simulator Controller/Config/Simulator Configuration.ini').is_file()), ensure_ascii=False)); return
    if (root / 'Binaries/Simulator Startup.exe').is_file():
        print('复用已安装的 Simulator Controller，不重复下载。', flush=True); ensure_stopped(root)
    else:
        managed = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'Programs/SimulatorController'
        if (managed / 'Binaries/Simulator Startup.exe').is_file():
            root = managed; ensure_stopped(root)
        else:
            print('下载官方便携版。', flush=True); install_portable(managed); root = managed
        save_root(root)
    validate_install(root)
    print('准备 iRacing 中文基础配置。', flush=True); prepare_config(root, docs)
    print('备份原配置并接入本地语音。', flush=True); connect_speech()
    print('安装和接入完成。请启动 Simulator Controller 和助手播报，再进游戏测试。', flush=True)

if __name__ == '__main__':
    try: main()
    except (OSError, ValueError, RuntimeError, requests.RequestException, configparser.Error) as e:
        raise SystemExit('一键安装未完成：' + str(e))
