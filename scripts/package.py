"""Real native binary checks and lossless, symlink-preserving archives."""
import gzip
import hashlib
import os
from pathlib import Path
import shutil
import stat
import struct
import tarfile
import zipfile


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def binary_arches(data):
    magic=data[:4]
    if magic in (b'\xca\xfe\xba\xbe',b'\xca\xfe\xba\xbf'):
        count=struct.unpack_from('>I',data,4)[0]
        stride=32 if magic[-1]==191 else 20
        arches=set()
        for i in range(count):
            cpu=struct.unpack_from('>I',data,8+i*stride)[0]
            arches.add({0x01000007:'x64',0x0100000c:'arm64'}.get(cpu,hex(cpu)))
        return 'darwin',arches
    if magic==b'\xcf\xfa\xed\xfe':
        cpu=struct.unpack_from('<I',data,4)[0]
        return 'darwin',{{0x01000007:'x64',0x0100000c:'arm64'}.get(cpu,hex(cpu))}
    if magic==b'\x7fELF':
        if len(data)<20 or data[4]!=2 or data[5] not in (1,2):raise ValueError('Invalid ELF header')
        cpu=struct.unpack_from('<H' if data[5]==1 else '>H',data,18)[0]
        return 'linux',{{62:'x64',183:'arm64'}.get(cpu,hex(cpu))}
    if data[:2]==b'MZ':
        if len(data)<64:raise ValueError('Truncated PE header')
        off=struct.unpack_from('<I',data,60)[0]
        if data[off:off+4]!=b'PE\0\0':raise ValueError('Invalid PE signature')
        cpu=struct.unpack_from('<H',data,off+4)[0]
        return 'win32',{{0x8664:'x64',0xaa64:'arm64',0x14c:'ia32'}.get(cpu,hex(cpu))}
    return None,set()


def native_inventory(app,platform,arch):
    result=[]
    for p in sorted(app.rglob('*')):
        if not p.is_file() or p.is_symlink():continue
        with p.open('rb') as f:data=f.read(65536)
        native,arches=binary_arches(data)
        if native:
            # Win64 ConPTY may legitimately bundle x86 helper programs.
            if native!=platform or (arch not in arches and not (platform=='win32' and arches=={'ia32'})):
                raise ValueError(f'Wrong native target {p}: {native}/{arches}, expected {platform}/{arch}')
            result.append({'path':p.relative_to(app).as_posix(),'platform':native,'arches':sorted(arches),'sha256':digest(p)})
        elif p.suffix in ('.node','.dll','.exe','.dylib','.so'):
            raise ValueError('Unrecognized native payload: '+str(p))
    if not result:raise ValueError('No native binaries found')
    return result


def inventory(app):
    result=[]
    for p in [app]+sorted(app.rglob('*')):
        mode=p.lstat().st_mode
        entry={'path':p.relative_to(app.parent).as_posix()}
        if os.name!='nt':entry['mode']=stat.S_IMODE(mode)
        if p.is_symlink():
            target=os.readlink(p)
            if os.path.isabs(target) or not p.exists() or not p.resolve().is_relative_to(app.resolve()):
                raise ValueError('Unsafe/dangling symlink: '+str(p))
            entry['link']=target
        elif p.is_dir():entry['directory']=True
        elif p.is_file():entry.update(size=p.stat().st_size,sha256=digest(p))
        else:raise ValueError('Unsupported filesystem object: '+str(p))
        result.append(entry)
    return result


def make_archive(app,outfile):
    inventory(app)  # refuse unsafe trees before packaging
    if outfile.name.endswith('.tar.gz'):
        with outfile.open('wb') as raw, gzip.GzipFile(fileobj=raw,mode='wb',mtime=0) as gz, tarfile.open(fileobj=gz,mode='w|') as tar:
            def normalize(info):
                info.uid=info.gid=0;info.uname=info.gname='';info.mtime=0
                return info
            tar.add(app,arcname=app.name,filter=normalize)
    else:
        with zipfile.ZipFile(outfile,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
            for p in [app]+sorted(app.rglob('*')):
                mode=p.lstat().st_mode
                name=p.relative_to(app.parent).as_posix()+('/' if stat.S_ISDIR(mode) else '')
                zi=zipfile.ZipInfo(name,(2020,1,1,0,0,0));zi.create_system=3;zi.external_attr=mode<<16
                zi.compress_type=zipfile.ZIP_DEFLATED
                if stat.S_ISLNK(mode):z.writestr(zi,os.readlink(p).encode())
                elif stat.S_ISDIR(mode):zi.external_attr|=0x10;z.writestr(zi,b'')
                else:
                    with p.open('rb') as src,z.open(zi,'w',force_zip64=True) as dst:shutil.copyfileobj(src,dst,1024*1024)


def unpack_archive(archive,dest):
    if dest.exists():raise ValueError('Extraction destination already exists: '+str(dest))
    dest.mkdir(parents=True)
    if archive.name.endswith('.tar.gz'):
        with tarfile.open(archive) as tar:tar.extractall(dest,filter='data')
        return
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:raise ValueError('ZIP CRC failure')
        for zi in z.infolist():
            p=dest/zi.filename
            if not p.resolve().is_relative_to(dest.resolve()):raise ValueError('ZIP traversal')
            mode=zi.external_attr>>16
            if stat.S_ISDIR(mode):p.mkdir(parents=True,exist_ok=True);p.chmod(stat.S_IMODE(mode))
            elif stat.S_ISLNK(mode):
                target=z.read(zi).decode()
                if os.path.isabs(target) or not (p.parent/target).resolve().is_relative_to(dest.resolve()):raise ValueError('ZIP symlink escape')
                p.parent.mkdir(parents=True,exist_ok=True);p.symlink_to(target)
            else:
                p.parent.mkdir(parents=True,exist_ok=True)
                with z.open(zi) as src,p.open('wb') as dst:shutil.copyfileobj(src,dst,1024*1024)
                p.chmod(stat.S_IMODE(mode))
