"""Verify a clean signed candidate; OS acceptance remains explicitly pending."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

SCENARIOS = (
    'native_exactly_once', 'browser_textarea', 'browser_contenteditable',
    'electron', 'terminal_editor', 'codex', 'teams', 'secure_field',
    'focus_change', 'clipboard_copy_race', 'nontext_clipboard', 'rapid_repeat',
    'startup_ready', 'wake_then_dictation', 'input_switch',
    'diagnostic_then_dictation', 'meeting_failure_then_dictation',
    'completed_meeting_then_dictation',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=root).strip():
        parser.error('release requires a clean tracked and untracked source tree')
    resources = args.bundle / 'Contents/Resources'
    if (resources / 'build-commit.txt').read_text().strip() != revision:
        parser.error('bundle revision does not match clean source')
    manifest = json.loads((resources / 'release-build.json').read_text())
    if manifest['revision'] != revision or manifest['dirty'] or manifest['kind'] != 'release':
        parser.error('bundle is not a release build')
    for relative, expected in manifest['assets'].items():
        path = resources / relative
        digest = hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()
        if digest != expected:
            parser.error('bundled asset integrity mismatch')
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(args.bundle)], check=True)
    report = dict(revision=revision, bundle_verified=True, signature_verified=True,
                  accepted=False, scenarios={name: 'pending' for name in SCENARIOS},
                  automated_tests='run full host pytest separately',
                  delivery_evidence='none; signature verification is not visible delivery')
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print('Candidate integrity verified; OS acceptance pending.')


if __name__ == '__main__':
    main()
