"""Shared installer/updater archive extraction; no GUI or third-party imports."""
import os
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

MAX_MEMBERS = 50000
MAX_UNPACKED_BYTES = 4 * 1024 ** 3


def _destination(root, name):
    if not name or '\\' in name or '\x00' in name:
        raise ValueError('Unsafe archive member name')
    relative = PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or ':' in name:
        raise ValueError('Archive member escapes destination')
    if any(PureWindowsPath(part).is_reserved() or part.endswith((' ', '.')) for part in relative.parts):
        raise ValueError('Unsafe Windows archive member name')
    target = root.joinpath(*relative.parts)
    current = target
    while current != root:
        if current.is_symlink():
            raise ValueError('Archive destination contains a symbolic link')
        current = current.parent
    if not target.resolve().is_relative_to(root):
        raise ValueError('Archive member escapes destination')
    return target


def extract_archive_safely(archive_path, destination):
    """Validate every member before writing; reject links/devices and oversized archives.

    The caller must own the destination directory; concurrent hostile local
    filesystem changes are outside this function's trust boundary.
    """
    root = Path(destination).resolve()
    archive_path = str(archive_path)
    if zipfile.is_zipfile(archive_path):
        archive = zipfile.ZipFile(archive_path)
        is_zip = True
    elif tarfile.is_tarfile(archive_path):
        archive = tarfile.open(archive_path, 'r:*')
        is_zip = False
    else:
        raise ValueError('Unsupported or corrupt archive')
    with archive:
        members = archive.infolist() if is_zip else archive.getmembers()
        if len(members) > MAX_MEMBERS:
            raise ValueError('Archive contains too many members')
        size = 0
        plan = []
        for member in members:
            name = member.filename if is_zip else member.name
            target = _destination(root, name)
            directory = member.is_dir() if is_zip else member.isdir()
            if is_zip:
                kind = stat.S_IFMT(member.external_attr >> 16)
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ValueError('Archive links and special files are not allowed')
                size += member.file_size
            else:
                if not (member.isfile() or directory):
                    raise ValueError('Archive links and special files are not allowed')
                size += member.size
            if size > MAX_UNPACKED_BYTES:
                raise ValueError('Archive exceeds unpacked size limit')
            plan.append((member, target, directory))
        for member, target, directory in plan:
            _destination(root, member.filename if is_zip else member.name)
            if directory:
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.open(member) if is_zip else archive.extractfile(member)
            with stream as source, open(target, 'wb') as output:
                shutil.copyfileobj(source, output)
            if not is_zip:
                os.chmod(target, member.mode & 0o777)
