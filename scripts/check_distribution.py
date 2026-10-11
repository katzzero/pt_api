"""Check release contents/metadata using only the standard library (Python3.8+)."""
import ast
from email.parser import BytesParser
from pathlib import Path
import sys
import tarfile
import zipfile


def check(folder):
    root = Path(__file__).resolve().parents[1]
    source = (root / 'pt_api.py').read_bytes()
    module = ast.parse(source.decode('utf-8-sig'))
    versions = [n.value.value for n in module.body
                if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
                and any(isinstance(t, ast.Name) and t.id == '__version__' for t in n.targets)]
    if len(versions) != 1:
        raise ValueError('A unique literal source version is required.')
    folder = Path(folder)
    wheels, sources = list(folder.glob('*.whl')), list(folder.glob('*.tar.gz'))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError('Exactly one wheel and one source distribution are required.')
    for archive in wheels + sources:
        if archive.suffix == '.whl':
            with zipfile.ZipFile(archive) as stream:
                data = {n: stream.read(n) for n in stream.namelist() if not n.endswith('/')}
            metadata_name = next(n for n in data if n.endswith('.dist-info/METADATA'))
        else:
            with tarfile.open(archive, 'r:gz') as stream:
                data = {}
                for member in stream.getmembers():
                    if member.isfile():
                        payload = stream.extractfile(member)
                        if payload is None:
                            raise ValueError('Missing source archive member payload.')
                        with payload:
                            data[member.name] = payload.read()
            metadata_name = min((n for n in data if n.endswith('/PKG-INFO')), key=len)
        module_names = [n for n in data if n == 'pt_api.py' or n.endswith('/pt_api.py')]
        if len(module_names) != 1 or data[module_names[0]] != source:
            raise ValueError('Distribution module differs from the audited source.')
        for filename in ('LICENSE', 'COPYING', 'NOTICE.md'):
            matches = [n for n in data if n.rsplit('/', 1)[-1] == filename]
            if len(matches) != 1 or data[matches[0]] != (root / filename).read_bytes():
                raise ValueError('Missing or modified distribution notice: ' + filename)
        for name in data:
            parts = name.replace('\\', '/').split('/')
            if (Path(name).suffix.lower() in ('.ptx', '.wav', '.aif', '.aiff', '.mxf', '.pyc')
                or '.env' in parts or '.git' in parts or '__pycache__' in parts):
                raise ValueError('Private media/configuration/cache included: ' + name)
        metadata = BytesParser().parsebytes(data[metadata_name])
        if (metadata['Name'] != 'pt_api' or metadata['Version'] != versions[0]
            or metadata['License'] != 'LGPL-2.1-or-later'
            or metadata['Requires-Python'] != '>=3.8' or metadata.get_all('Requires-Dist')):
            raise ValueError('Distribution metadata disagrees with the audited package contract.')
        print(archive.name + ': source, version, LGPL notices and media-free contents verified')


if __name__ == '__main__':
    check(sys.argv[1] if len(sys.argv) == 2 else 'dist')
