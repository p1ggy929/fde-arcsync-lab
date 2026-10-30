"""One-click upgrades retain configuration and never stop an unverified service."""
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import launch
import runtime_process


class LauncherTests(unittest.TestCase):
    def test_stale_server_restarts_with_previous_environment(self):
        process=MagicMock();process.poll.side_effect=[None,0];process.wait.return_value=0
        environment={'PATH':'/usr/bin','ARCSYNC_DB':'/tmp/existing.sqlite3'}
        response=MagicMock();response.__enter__.return_value.read.return_value=json.dumps({'build_id':'old'}).encode()
        opener=MagicMock();opener.open.return_value=response
        with patch.object(sys,'argv',['launch','--no-browser']),patch.object(launch,'port_in_use',return_value=True),patch.object(launch,'health',return_value=True),patch.object(launch,'build_opener',return_value=opener),patch.object(launch,'prepare_environment',return_value=Path('/python')),patch.object(launch.subprocess,'Popen',return_value=process) as start,patch.object(runtime_process,'inspect_server',return_value=(123,environment)),patch.object(runtime_process,'stop_verified_server') as stop,patch.object(launch,'say'):
            self.assertEqual(launch.main(),0)
        stop.assert_called_once_with(123)
        self.assertEqual(start.call_args.kwargs['env']['ARCSYNC_DB'],'/tmp/existing.sqlite3')
    def test_unverified_server_is_never_stopped(self):
        response=MagicMock();response.__enter__.return_value.read.return_value=b'{"build_id":"old"}'
        opener=MagicMock();opener.open.return_value=response
        with patch.object(sys,'argv',['launch','--no-browser']),patch.object(launch,'port_in_use',return_value=True),patch.object(launch,'health',return_value=True),patch.object(launch,'build_opener',return_value=opener),patch.object(runtime_process,'inspect_server',side_effect=RuntimeError('无法核验')),patch.object(runtime_process,'stop_verified_server') as stop:
            with self.assertRaises(RuntimeError):launch.main()
        stop.assert_not_called()
    def test_current_server_is_reused(self):
        response=MagicMock();response.__enter__.return_value.read.return_value=json.dumps({'build_id':launch.runpy.run_path(str(launch.ROOT/'src/arcsync/core/runtime.py'))['BUILD_ID']}).encode()
        opener=MagicMock();opener.open.return_value=response
        with patch.object(sys,'argv',['launch','--no-browser']),patch.object(launch,'port_in_use',return_value=True),patch.object(launch,'health',return_value=True),patch.object(launch,'build_opener',return_value=opener),patch.object(launch,'prepare_environment') as prepare,patch.object(launch,'say'):
            self.assertEqual(launch.main(),0)
        prepare.assert_not_called()
