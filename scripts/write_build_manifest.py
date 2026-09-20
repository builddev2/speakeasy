"""Embed exact build configuration and asset integrity before signing."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

resources = Path(sys.argv[1]) / 'Contents/Resources'
revision = (resources / 'build-commit.txt').read_text().strip()
assets = ['model/config.json', 'model/model.safetensors',
          'diarization/segmentation.onnx', 'diarization/embedding.onnx',
          'native/SpeakeasySystemAudioCapture']
assets += [str(path.relative_to(resources)) for path in sorted((resources / 'frontend').rglob('*')) if path.is_file()]
for name in ('dock', 'meetings', 'training', 'diagnostic'):
    if not (resources / 'frontend' / f'{name}.html').is_file():
        raise SystemExit('missing frontend entrypoint')
hashes = {}
for name in assets:
    with (resources / name).open('rb') as source:
        hashes[name] = hashlib.file_digest(source, 'sha256').hexdigest()
manifest = dict(revision=revision.removesuffix('-dirty'), dirty=revision.endswith('-dirty'),
                kind='release' if sys.argv[2] == '1' else 'development',
                configuration={'dictation': 'batch', 'sample_rate': 16000, 'model': 'mlx-community/parakeet-tdt-0.6b-v2'},
                versions={name: importlib.metadata.version(name) for name in ('mlx', 'parakeet-mlx', 'numpy', 'sherpa-onnx')},
                assets=hashes)
(resources / 'release-build.json').write_text(json.dumps(manifest, indent=2) + '\n')
