"""Offline regression tests for installer, updater and credential boundaries."""
import io
import runpy
import stat
import tarfile
import zipfile
from unittest.mock import Mock

import pytest
import archive_utils
from archive_utils import extract_archive_safely
from pyMediaTools.core import gladia_transcription as gladia
from pyMediaTools.core.update import check_latest_release
from pyMediaTools.core.ytdlp_updater import YtDlpUpdater


@pytest.mark.parametrize('name', ['../outside', '/outside', 'C:/outside', 'a\\..\\outside', 'a/file:stream', 'NUL', 'a/../b'])
def test_zip_rejects_unsafe_names_before_any_write(tmp_path, name):
    path = tmp_path / 'archive.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('safe.txt', 'safe')
        archive.writestr(name, 'evil')
    with pytest.raises(ValueError):
        extract_archive_safely(path, tmp_path / 'out')
    assert not (tmp_path / 'out/safe.txt').exists()
    assert not (tmp_path / 'outside').exists()


@pytest.mark.parametrize('kind', [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE])
def test_tar_rejects_links_and_special_files(tmp_path, kind):
    path = tmp_path / 'archive.tar.gz'
    with tarfile.open(path, 'w:gz') as archive:
        member = tarfile.TarInfo('link')
        member.type = kind
        member.linkname = '../outside'
        archive.addfile(member)
    with pytest.raises(ValueError):
        extract_archive_safely(path, tmp_path / 'out')


def test_tar_rejects_parent_traversal(tmp_path):
    path = tmp_path / 'bad.tar'
    with tarfile.open(path, 'w') as archive:
        member = tarfile.TarInfo('../outside')
        member.size = 1
        archive.addfile(member, io.BytesIO(b'x'))
    with pytest.raises(ValueError):
        extract_archive_safely(path, tmp_path / 'out')
    assert not (tmp_path / 'outside').exists()


@pytest.mark.parametrize('suffix', ['.zip', '.tar.gz'])
def test_normal_archives_extract(tmp_path, suffix):
    path = tmp_path / ('archive' + suffix)
    if suffix == '.zip':
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('package/file.txt', b'hello')
    else:
        with tarfile.open(path, 'w:gz') as archive:
            member = tarfile.TarInfo('package/file.txt')
            member.size = 5
            archive.addfile(member, io.BytesIO(b'hello'))
    extract_archive_safely(path, tmp_path / 'out')
    assert (tmp_path / 'out/package/file.txt').read_bytes() == b'hello'


def test_zip_symlink_and_size_limit_rejected(tmp_path, monkeypatch):
    path = tmp_path / 'link.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        link = zipfile.ZipInfo('link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, '../outside')
    with pytest.raises(ValueError):
        extract_archive_safely(path, tmp_path / 'out')
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('file.txt', '1234')
    monkeypatch.setattr(archive_utils, 'MAX_UNPACKED_BYTES', 3)
    with pytest.raises(ValueError):
        extract_archive_safely(path, tmp_path / 'out')


@pytest.mark.parametrize('url', [
    'http://api.gladia.io/v2/task', 'https://api.gladia.io.evil.example/v2/task',
    'https://user@api.gladia.io/v2/task', 'https://api.gladia.io:444/v2/task',
    'https://evil.example/v2/task', 'https://api.gladia.io/\nfoo', None,
])
def test_gladia_does_not_send_credentials_to_untrusted_url(url, monkeypatch):
    get = Mock()
    monkeypatch.setattr(gladia.requests, 'get', get)
    with pytest.raises(ValueError):
        gladia._gladia_poll_result(url, 'test-key')
    get.assert_not_called()


def test_gladia_poll_does_not_follow_cross_origin_redirect(monkeypatch):
    response = Mock(status_code=302, text='sensitive signed URL')
    get = Mock(return_value=response)
    monkeypatch.setattr(gladia.requests, 'get', get)
    with pytest.raises(RuntimeError, match='HTTP 302') as error:
        gladia._gladia_poll_result('https://api.gladia.io/v2/task', 'test-key')
    assert get.call_args.kwargs['allow_redirects'] is False
    assert 'sensitive' not in str(error.value)


def test_gladia_poll_success(monkeypatch):
    response = Mock(status_code=200)
    response.json.return_value = {'status': 'done'}
    monkeypatch.setattr(gladia.requests, 'get', Mock(return_value=response))
    assert gladia._gladia_poll_result('https://api.gladia.io/v2/task', 'test-key') == {'status': 'done'}


def test_manual_key_script_has_no_import_time_requests(monkeypatch):
    import requests
    request = Mock(side_effect=AssertionError('unexpected network'))
    monkeypatch.setattr(requests, 'get', request)
    runpy.run_path('tests/import requests.py', run_name='security_test')
    request.assert_not_called()


def test_release_source_is_current_repository(monkeypatch):
    import requests
    response = Mock(status_code=200)
    response.json.return_value = {'tag_name': 'v1.2.3'}
    get = Mock(return_value=response)
    monkeypatch.setattr(requests, 'get', get)
    check_latest_release()
    assert get.call_args.args[0] == 'https://api.github.com/repos/xvacts/pyMediaConvert/releases/latest'


def test_update_version_cannot_control_download_paths(tmp_path, monkeypatch):
    updater = YtDlpUpdater(str(tmp_path / 'yt_dlp'))
    monkeypatch.setattr(updater, 'get_remote_version_from_github', lambda: '../../escape')
    request = Mock(side_effect=AssertionError('unexpected download'))
    monkeypatch.setattr('requests.get', request)
    success, _ = updater.update_from_github()
    assert success is False
    request.assert_not_called()


@pytest.mark.parametrize('version,tag', [('2026.10.21', '2026.10.21'), ('2026.7.4', '2026.07.04')])
def test_build_downloads_installed_release_not_master(monkeypatch, version, tag):
    import install_dependencies as installer
    monkeypatch.setattr(installer, 'package_version', lambda name: version)
    assert installer.get_ytdlp_archive_url() == f'https://github.com/yt-dlp/yt-dlp/archive/refs/tags/{tag}.zip'


@pytest.mark.parametrize('version', ['../../escape', '2026.10.21.dev0', '2026.13.40'])
def test_build_rejects_nonrelease_or_invalid_version(monkeypatch, version):
    import install_dependencies as installer
    monkeypatch.setattr(installer, 'package_version', lambda name: version)
    with pytest.raises(ValueError):
        installer.get_ytdlp_archive_url()
