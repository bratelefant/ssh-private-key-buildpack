import base64
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


HELPER = Path(__file__).resolve().parents[1] / 'bin/additional-keys'
REPOSITORY = 'github.com/bratelefant/planb-client-sdk.git'


class AdditionalKeysTest(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix='ssh-buildpack-test-')
        self.root = Path(self.workspace.name)
        self.env_dir = self.root / 'env'
        self.ssh_dir = self.root / 'account' / '.ssh'
        self.bin_dir = self.root / 'bin'
        self.app_dir = self.root / 'app'
        for directory in [self.env_dir, self.ssh_dir, self.bin_dir, self.app_dir]:
            directory.mkdir(parents=True)
        self.export_file = self.root / 'export'
        self.log = self.root / 'ssh.log'
        self.global_config = self.app_dir / '.gitconfig'
        self.global_config.write_text('[user]\n\tname = Existing build user\n')
        self.previous_ssh = 'Host github.com\n  IdentityFile /legacy/white-label-key\n'
        (self.ssh_dir / 'config').write_text(self.previous_ssh)
        (self.ssh_dir / 'id_rsa').write_text('existing white-label key')
        self.key = self.root / 'test-key'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(self.key)], check=True)
        self.private_key = self.key.read_bytes()
        self.remote = self.root / 'remote.git'
        subprocess.run(['git', 'init', '--bare', '--quiet', str(self.remote)], check=True)
        ssh = self.bin_dir / 'ssh'
        ssh.write_text('''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['TEST_SSH_LOG'], 'a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
if os.environ.get('TEST_DENY_ACCESS'):
    sys.exit(1)
os.execvp('git-upload-pack', ['git-upload-pack', os.environ['TEST_GIT_REMOTE']])
''')
        ssh.chmod(0o700)
        scanner = self.bin_dir / 'ssh-keyscan'
        scanner.write_text('#!/bin/sh\nprintf "github.com "\ncat "$TEST_PUBLIC_KEY"\n')
        scanner.chmod(0o700)
        self.env = {
            **os.environ,
            'PATH': str(self.bin_dir) + os.pathsep + os.environ['PATH'],
            'GIT_CONFIG_GLOBAL': str(self.global_config),
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_SSH_COMMAND': shlex.quote(str(ssh)),
            'GIT_SSH_VARIANT': 'ssh',
            'TEST_SSH_LOG': str(self.log),
            'TEST_GIT_REMOTE': str(self.remote),
            'TEST_PUBLIC_KEY': str(self.key) + '.pub',
        }
        self.key_directories = set()

    def tearDown(self):
        for directory in self.key_directories:
            shutil.rmtree(directory)
        self.workspace.cleanup()

    def configure(self, suffix='CLIENT_SDK', repository=REPOSITORY):
        (self.env_dir / ('SSH_KEY_' + suffix)).write_bytes(base64.b64encode(self.private_key))
        (self.env_dir / ('SSH_KEY_' + suffix + '_REPOSITORY')).write_text(repository)

    def run_helper(self):
        result = subprocess.run(['bash', str(HELPER), str(self.env_dir), str(self.ssh_dir), str(self.export_file)], env=self.env, capture_output=True, text=True)
        for line in (self.ssh_dir / 'config').read_text().splitlines():
            if 'IdentityFile "' in line:
                self.key_directories.add(Path(line.split('"')[1]).parent)
        self.assertNotIn(self.private_key.decode().strip(), result.stdout + result.stderr)
        self.assertNotIn(base64.b64encode(self.private_key).decode(), result.stdout + result.stderr)
        return result

    def test_legacy_only_is_a_noop(self):
        before = self.global_config.read_text()
        self.assertEqual(self.run_helper().returncode, 0)
        self.assertEqual((self.ssh_dir / 'config').read_text(), self.previous_ssh)
        self.assertEqual(self.global_config.read_text(), before)
        self.assertFalse(self.export_file.exists())
        self.assertFalse(self.log.exists())

    def test_routes_sdk_urls_and_preserves_white_label(self):
        self.configure()
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(self.previous_ssh, (self.ssh_dir / 'config').read_text())
        self.assertEqual((self.ssh_dir / 'id_rsa').read_text(), 'existing white-label key')
        config = subprocess.check_output(['/usr/bin/ssh', '-G', '-F', str(self.ssh_dir / 'config'), 'scalingo-client-sdk'], stderr=subprocess.DEVNULL, text=True)
        self.assertIn('hostname github.com\n', config)
        self.assertIn('identitiesonly yes\n', config)
        key_path = Path(next(line.split(' ', 1)[1] for line in config.splitlines() if line.startswith('identityfile ')))
        self.assertEqual(key_path.read_bytes(), self.private_key)
        self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(key_path.is_relative_to(self.app_dir))
        for prefix in ['ssh://git@github.com/', 'git@github.com:', 'https://github.com/']:
            subprocess.run(['git', 'ls-remote', prefix + 'bratelefant/planb-client-sdk.git', 'HEAD'], env=self.env, check=True, capture_output=True)
            self.assertIn('git@scalingo-client-sdk', json.loads(self.log.read_text().splitlines()[-1]))
        subprocess.run(['git', 'ls-remote', 'ssh://git@github.com/bratelefant/planb-white-label.git', 'HEAD'], env=self.env, check=True, capture_output=True)
        self.assertIn('git@github.com', json.loads(self.log.read_text().splitlines()[-1]))
        self.assertIn('Existing build user', self.global_config.read_text())

    def test_repeat_and_multiple_keys_keep_one_mapping_each(self):
        self.configure()
        self.configure('SECOND', 'github.com/example/another.git')
        for _ in range(2):
            result = self.run_helper()
            self.assertEqual(result.returncode, 0, result.stderr)
        config = (self.ssh_dir / 'config').read_text()
        self.assertEqual(config.count('Host scalingo-client-sdk\n'), 1)
        self.assertEqual(config.count('Host scalingo-second\n'), 1)
        values = subprocess.check_output(['git', 'config', '--global', '--get-all', 'url.ssh://git@scalingo-client-sdk/bratelefant/planb-client-sdk.git.insteadOf'], env=self.env, text=True)
        self.assertEqual(len(values.splitlines()), 3)

    def test_exports_absolute_git_config_for_meteor_subprocesses(self):
        self.configure()
        self.assertEqual(self.run_helper().returncode, 0)
        child_env = dict(self.env)
        del child_env['GIT_CONFIG_GLOBAL']
        output = subprocess.check_output(['bash', '-c', 'source "$1"; git config --global --get user.name', 'test', str(self.export_file)], env=child_env, text=True)
        self.assertEqual(output.strip(), 'Existing build user')
        self.assertIn(str(self.global_config), self.export_file.read_text())

    def test_missing_and_invalid_keys_fail_before_access(self):
        self.configure()
        key = self.env_dir / 'SSH_KEY_CLIENT_SDK'
        for value in [b'', b'not a valid key']:
            key.write_bytes(value)
            result = self.run_helper()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('SSH_KEY_CLIENT_SDK', result.stderr)
            self.assertFalse(self.log.exists())
            self.assertFalse(self.export_file.exists())

    def test_invalid_repository_and_denied_access_fail_the_build(self):
        self.configure(repository='https://github.com/example/repo.git')
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('host/path/repository.git', result.stderr)
        self.assertFalse(self.log.exists())
        self.configure()
        self.env['TEST_DENY_ACCESS'] = '1'
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Read access', result.stderr)
        self.assertFalse(self.export_file.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
