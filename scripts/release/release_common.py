"""Shared paths and helpers for the versioned release scripts.

Every release script takes the target version on the command line instead of
being copied and edited for each release.
"""
import argparse,hashlib
from pathlib import Path

SOURCE=Path(__file__).resolve().parents[2]
# The portable Inno Setup lives beside the repository in development/.
ISCC=SOURCE.parent/'tools/inno-portable/tools/ISCC.exe'
BUILD_ROOT=Path('D:/iRacing安装构建')
BACKUP_ROOT=Path('D:/iRacing中文播报助手安装备份')

def build_dir(version):return BUILD_ROOT/version
def payload(version):return build_dir(version)/'payload'
def install_dir(version):return BACKUP_ROOT/'完整安装文件'/version
def parts_dir(version):return BACKUP_ROOT/'GitHub分卷'/version
def patch_dir(version):return BACKUP_ROOT/'增量更新'/version
def installer(version):return install_dir(version)/f'iracing-chinese-assistant-{version}-setup.exe'
def updater(version,old):return patch_dir(version)/f'iracing-chinese-assistant-{version}-update-from-{old}.exe'

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()

def parser(description,base=False,olds=False):
    p=argparse.ArgumentParser(description=description)
    p.add_argument('version',help='release being built, e.g. 0.3.0')
    if base:p.add_argument('--base',required=True,help='previous full release whose verified payload is cloned')
    if olds:p.add_argument('--from',dest='olds',nargs='+',required=True,metavar='OLD',help='installed versions that receive an update patch')
    return p
