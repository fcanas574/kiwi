"""Startup URL contract and trusted-CA continuity across DHCP address changes."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

script = Path(__file__).parents[1] / 'deploy/prepare_network.py'
if not script.exists():
    script = Path(__file__).with_name('prepare_network.py')
spec = importlib.util.spec_from_file_location('prepare_network', script)
network = importlib.util.module_from_spec(spec)
spec.loader.exec_module(network)


class NetworkPreparationTests(unittest.TestCase):
    def test_address_change_updates_config_qr_and_setup_page_without_touching_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            root = home / 'kiwi-https-pruebas'
            root.mkdir()
            pi = home / 'kiwi-pruebas/pi'
            pi.mkdir(parents=True)
            config = {'server': {'public_url': 'https://kite.local:8000'}, 'recognizer': {'endpoint': 'https://example.org/predict'}}
            (pi / 'config.yaml').write_text(yaml.safe_dump(config))
            (root / 'server-config.yaml').write_text(yaml.safe_dump({'server': {'tls_certfile': 'keep.crt'}}))
            calls = []

            def qr_command(*args, **kwargs):
                calls.append(args[-1])
                Path(args[args.index('-o') + 1]).write_bytes(b'qr-png')
                return ''

            with patch.object(network, 'ensure_certificate'), patch.object(network, 'run', side_effect=qr_command):
                for address in ('192.168.58.109', '192.168.58.110'):
                    with patch.object(network, 'find_address', return_value=address):
                        network.prepare(home)
                    camera = f'https://{address}:8000/camera'
                    self.assertEqual((root / 'camera-url.txt').read_text().strip(), camera)
                    updated = yaml.safe_load((pi / 'config.yaml').read_text())
                    self.assertEqual(updated['server']['public_url'] + '/camera', camera)
                    self.assertEqual(updated['recognizer'], config['recognizer'])
                    self.assertEqual(calls[-1], camera)
                    self.assertEqual((pi / 'kiwi_pi/static/phone-qr.png').read_bytes(), b'qr-png')
                    self.assertEqual(yaml.safe_load((root / 'server-config.yaml').read_text())['server']['tls_certfile'], 'keep.crt')

    def test_no_wifi_preserves_existing_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            pi = home / 'kiwi-pruebas/pi'
            pi.mkdir(parents=True)
            original = 'server:\n  public_url: https://192.168.58.109:8000\n'
            (pi / 'config.yaml').write_text(original)
            with patch.object(network, 'find_address', return_value=None), patch.object(network.time, 'sleep'):
                network.prepare(home)
            self.assertEqual((pi / 'config.yaml').read_text(), original)

    @unittest.skipUnless('OpenSSL' in subprocess.run(['openssl', 'version'], capture_output=True, text=True).stdout,
                         'requires Raspberry Pi OpenSSL hostname/IP verification')
    def test_changed_ip_reissues_certificate_under_same_trusted_ca(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ext = root / 'ca.cnf'
            ext.write_text('[req]\ndistinguished_name=dn\nx509_extensions=ca\nprompt=no\n[dn]\nCN=Kiwi test CA\n[ca]\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n')
            network.run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
                        '-config', str(ext), '-out', str(root / 'ca.crt'), '-keyout', str(root / 'ca.key'))
            network.run('openssl', 'genrsa', '-out', str(root / 'server.key'), '2048')
            # An existing leaf is required for the installed system backup.
            (root / 'server.crt').write_bytes((root / 'ca.crt').read_bytes())
            ca = (root / 'ca.crt').read_bytes()
            key = (root / 'server.key').read_bytes()
            network.ensure_certificate(root, '192.168.58.109')
            first = (root / 'server.crt').read_bytes()
            network.ensure_certificate(root, '192.168.58.110')
            second = (root / 'server.crt').read_bytes()
            self.assertNotEqual(first, second)
            self.assertEqual((root / 'ca.crt').read_bytes(), ca)
            self.assertEqual((root / 'server.key').read_bytes(), key)
            network.run('openssl', 'verify', '-verify_ip', '192.168.58.110', '-CAfile', str(root / 'ca.crt'), str(root / 'server.crt'))
            network.run('openssl', 'verify', '-verify_hostname', 'kite.local', '-CAfile', str(root / 'ca.crt'), str(root / 'server.crt'))
            network.ensure_certificate(root, '192.168.58.110')
            self.assertEqual((root / 'server.crt').read_bytes(), second)


if __name__ == '__main__':
    unittest.main()
