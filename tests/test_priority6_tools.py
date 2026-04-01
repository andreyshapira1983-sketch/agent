"""
Priority-6 coverage: tool modules at 0%.
  - tools/blender_tool.py       (subprocess mock)
  - tools/video_edit_tool.py    (subprocess mock)
  - tools/voice_call_tool.py    (HTTP mock)
  - tools/figma_tool.py         (HTTP mock)
  - tools/mobile_tool.py        (subprocess mock)
  - tools/image_edit_tool.py    (Pillow + subprocess mock)
  - tools/cad_tool.py           (ezdxf + subprocess mock)
  - tools/upwork_tool.py        (HTTP mock)
  - perception/speech_synthesizer.py (OpenAI mock)
"""
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════════════════
# 1. tools/blender_tool.py
# ═══════════════════════════════════════════════════════════════════════════
from tools.blender_tool import BlenderTool, _find_blender, _run_blender


class TestFindBlender(unittest.TestCase):
    def test_custom_path_exists(self):
        with tempfile.NamedTemporaryFile(suffix='.exe', delete=False) as f:
            path = f.name
        try:
            self.assertEqual(_find_blender(path), path)
        finally:
            os.unlink(path)

    def test_custom_path_not_exists(self):
        result = _find_blender('/nonexistent/blender.exe')
        # Falls through to shutil.which and candidates
        self.assertTrue(result is None or isinstance(result, str))

    def test_none(self):
        result = _find_blender(None)
        # May or may not find blender — just don't crash
        self.assertTrue(result is None or isinstance(result, str))


class TestRunBlender(unittest.TestCase):
    def test_no_exe(self):
        r = _run_blender(None, ['--version'])
        self.assertFalse(r['ok'])
        self.assertIn('error', r)

    @patch('tools.blender_tool.subprocess.run')
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='Blender 4.0', stderr='')
        r = _run_blender('/fake/blender', ['--version'])
        self.assertTrue(r['ok'])

    @patch('tools.blender_tool.subprocess.run')
    def test_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired('blender', 30)
        r = _run_blender('/fake/blender', ['--version'], timeout=30)
        self.assertFalse(r['ok'])
        self.assertIn('Таймаут', r['error'])


class TestBlenderTool(unittest.TestCase):
    def test_init_no_blender(self):
        bt = BlenderTool(blender_path='/nonexistent')
        # _exe may be None
        r = bt.version()
        self.assertFalse(r['ok'])

    def test_run_dispatcher_unknown(self):
        bt = BlenderTool(blender_path='/nonexistent')
        r = bt.run(action='unknown_action')
        self.assertFalse(r['ok'])
        self.assertIn('Неизвестный', r['error'])

    def test_export_bad_format(self):
        bt = BlenderTool(blender_path='/nonexistent')
        r = bt.export('scene.blend', 'out.xyz', fmt='xyz')
        self.assertFalse(r['ok'])

    def test_convert_bad_input_format(self):
        bt = BlenderTool(blender_path='/nonexistent')
        r = bt.convert('model.xyz', 'out.obj')
        self.assertFalse(r['ok'])

    def test_convert_bad_output_format(self):
        bt = BlenderTool(blender_path='/nonexistent')
        r = bt.convert('model.obj', 'out.xyz')
        self.assertFalse(r['ok'])


# ═══════════════════════════════════════════════════════════════════════════
# 2. tools/video_edit_tool.py
# ═══════════════════════════════════════════════════════════════════════════
from tools.video_edit_tool import VideoEditTool


class TestVideoEditToolNoFfmpeg(unittest.TestCase):
    """Test behavior when ffmpeg is not available."""

    def setUp(self):
        self.vt = VideoEditTool()

    @patch('tools.video_edit_tool._ff', return_value=None)
    def test_info_no_ff(self, _):
        r = self.vt.info('video.mp4')
        self.assertFalse(r['ok'])

    @patch('tools.video_edit_tool._ff', return_value=None)
    def test_cut_no_ff(self, _):
        r = self.vt.cut('in.mp4', 'out.mp4', '00:00:00', '00:00:10')
        self.assertFalse(r['ok'])

    def test_run_dispatcher_unknown(self):
        r = self.vt.run(action='unknown_action')
        self.assertFalse(r['ok'])


class TestVideoEditToolWithFfmpeg(unittest.TestCase):
    """Test with mocked ffmpeg subprocess."""

    def setUp(self):
        self.vt = VideoEditTool()

    @patch('tools.video_edit_tool.subprocess.run')
    @patch('tools.video_edit_tool._ff_probe', return_value='/usr/bin/ffprobe')
    def test_info_success(self, _probe, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"format": {"duration": "10.5", "filename": "v.mp4"}, "streams": []}',
            stderr='',
        )
        r = self.vt.info('v.mp4')
        self.assertTrue(r.get('ok', True))  # info returns parsed dict

    @patch('tools.video_edit_tool.subprocess.run')
    @patch('tools.video_edit_tool._ff', return_value='/usr/bin/ffmpeg')
    def test_cut_success(self, _ff, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='', stderr='')
        r = self.vt.cut('in.mp4', 'out.mp4', '00:00:00', '00:00:10')
        self.assertTrue(r['ok'])

    @patch('tools.video_edit_tool.subprocess.run')
    @patch('tools.video_edit_tool._ff', return_value='/usr/bin/ffmpeg')
    def test_mute_success(self, _ff, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='', stderr='')
        r = self.vt.mute('in.mp4', 'out.mp4')
        self.assertTrue(r['ok'])


# ═══════════════════════════════════════════════════════════════════════════
# 3. tools/voice_call_tool.py
# ═══════════════════════════════════════════════════════════════════════════
from tools.voice_call_tool import VoiceCallTool


class TestVoiceCallToolNoCredentials(unittest.TestCase):
    def test_twilio_no_creds(self):
        vt = VoiceCallTool()
        r = vt.twilio_sms('+1234567890', 'Hello')
        self.assertFalse(r['ok'])
        self.assertIn('TWILIO', r['error'])

    def test_vonage_no_creds(self):
        vt = VoiceCallTool()
        r = vt.vonage_sms('+1234567890', 'Hello')
        self.assertFalse(r['ok'])
        self.assertIn('VONAGE', r['error'])

    def test_run_unknown_action(self):
        vt = VoiceCallTool()
        r = vt.run(action='unknown')
        self.assertFalse(r['ok'])


class TestVoiceCallToolWithCreds(unittest.TestCase):
    @patch('tools.voice_call_tool.urllib.request.urlopen')
    def test_twilio_sms_success(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = json.dumps({'sid': 'SM123'}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        vt = VoiceCallTool(twilio_sid='AC123', twilio_token='tok',
                           twilio_from='+1111')
        r = vt.twilio_sms('+2222', 'Test')
        self.assertTrue(r['ok'])

    @patch('tools.voice_call_tool.urllib.request.urlopen')
    def test_twilio_call_success(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = json.dumps({'sid': 'CA123'}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        vt = VoiceCallTool(twilio_sid='AC123', twilio_token='tok',
                           twilio_from='+1111')
        r = vt.twilio_call('+2222', 'Hello voice')
        self.assertTrue(r['ok'])

    @patch('tools.voice_call_tool.urllib.request.urlopen')
    def test_vonage_sms_success(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = json.dumps({'messages': [{'status': '0'}]}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        vt = VoiceCallTool(vonage_key='key', vonage_secret='sec',
                           vonage_from='+1111')
        r = vt.vonage_sms('+2222', 'Test')
        self.assertTrue(r['ok'])


# ═══════════════════════════════════════════════════════════════════════════
# 4. tools/figma_tool.py
# ═══════════════════════════════════════════════════════════════════════════
from tools.figma_tool import FigmaTool


class TestFigmaToolNoToken(unittest.TestCase):
    def test_no_token(self):
        ft = FigmaTool(access_token='')
        r = ft.get_me()
        self.assertIn('error', r)

    def test_run_unknown(self):
        ft = FigmaTool(access_token='fake')
        r = ft.run(action='nonexistent')
        self.assertIn('error', r)


class TestFigmaToolWithToken(unittest.TestCase):
    @patch('tools.figma_tool.urllib.request.urlopen')
    def test_get_me(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = json.dumps({'id': '123', 'handle': 'user'}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        ft = FigmaTool(access_token='fake_token')
        r = ft.get_me()
        self.assertTrue(r.get('ok', True))

    @patch('tools.figma_tool.urllib.request.urlopen')
    def test_list_pages(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = json.dumps({
            'document': {'children': [{'id': 'page1', 'name': 'Page 1', 'children': []}]}
        }).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        ft = FigmaTool(access_token='tok')
        r = ft.list_pages('file_key_123')
        self.assertIn('pages', r)


# ═══════════════════════════════════════════════════════════════════════════
# 5. tools/mobile_tool.py
# ═══════════════════════════════════════════════════════════════════════════
from tools.mobile_tool import MobileTool, _find_adb


class TestFindAdb(unittest.TestCase):
    def test_none_input(self):
        result = _find_adb(None)
        self.assertTrue(result is None or isinstance(result, str))

    def test_custom_path(self):
        with tempfile.NamedTemporaryFile(suffix='.exe', delete=False) as f:
            path = f.name
        try:
            self.assertEqual(_find_adb(path), path)
        finally:
            os.unlink(path)


class TestMobileToolNoAdb(unittest.TestCase):
    def test_list_devices_no_adb(self):
        mt = MobileTool(adb_path='/nonexistent')
        r = mt.list_devices()
        self.assertFalse(r['ok'])

    def test_run_unknown_action(self):
        mt = MobileTool(adb_path='/nonexistent')
        r = mt.run(action='unknown_action')
        self.assertFalse(r['ok'])


class TestMobileToolWithAdb(unittest.TestCase):
    @patch('tools.mobile_tool.subprocess.run')
    def test_list_devices(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='List of devices attached\nemulator-5554\tdevice\n',
            stderr='',
        )
        mt = MobileTool()
        mt._adb = '/usr/bin/adb'
        r = mt.list_devices()
        self.assertTrue(r['ok'])

    @patch('tools.mobile_tool.subprocess.run')
    def test_shell(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='result', stderr='',
        )
        mt = MobileTool()
        mt._adb = '/usr/bin/adb'
        r = mt.shell('ls /sdcard')
        self.assertTrue(r['ok'])


# ═══════════════════════════════════════════════════════════════════════════
# 6. tools/image_edit_tool.py
# ═══════════════════════════════════════════════════════════════════════════
from tools.image_edit_tool import ImageEditTool


class TestImageEditToolNoPillow(unittest.TestCase):
    @patch('tools.image_edit_tool._pillow_ok', return_value=False)
    def test_info_no_pillow(self, _):
        it = ImageEditTool()
        r = it.info('image.png')
        self.assertFalse(r['ok'])
        self.assertIn('Pillow', r.get('error', '') + r.get('install', ''))

    def test_run_unknown_action(self):
        it = ImageEditTool()
        r = it.run(action='nonexistent')
        self.assertFalse(r['ok'])


class TestImageEditToolWithPillow(unittest.TestCase):
    def _make_test_image(self):
        """Create a minimal test PNG."""
        try:
            from PIL import Image
            img = Image.new('RGB', (100, 100), color='red')
            tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            img.save(tmp.name)
            tmp.close()
            return tmp.name
        except ImportError:
            return None

    def test_info(self):
        path = self._make_test_image()
        if not path:
            self.skipTest('Pillow not installed')
        try:
            it = ImageEditTool()
            r = it.info(path)
            self.assertTrue(r['ok'])
            self.assertEqual(r['width'], 100)
        finally:
            os.unlink(path)

    def test_resize(self):
        path = self._make_test_image()
        if not path:
            self.skipTest('Pillow not installed')
        dst = path.replace('.png', '_resized.png')
        try:
            it = ImageEditTool()
            r = it.resize(path, dst, width=50, height=50)
            self.assertTrue(r['ok'])
            self.assertTrue(os.path.exists(dst))
        finally:
            for p in (path, dst):
                if os.path.exists(p):
                    os.unlink(p)

    def test_grayscale(self):
        path = self._make_test_image()
        if not path:
            self.skipTest('Pillow not installed')
        dst = path.replace('.png', '_gray.png')
        try:
            it = ImageEditTool()
            r = it.grayscale(path, dst)
            self.assertTrue(r['ok'])
        finally:
            for p in (path, dst):
                if os.path.exists(p):
                    os.unlink(p)

    def test_thumbnail(self):
        path = self._make_test_image()
        if not path:
            self.skipTest('Pillow not installed')
        dst = path.replace('.png', '_thumb.png')
        try:
            it = ImageEditTool()
            r = it.thumbnail(path, dst, max_width=32, max_height=32)
            self.assertTrue(r['ok'])
        finally:
            for p in (path, dst):
                if os.path.exists(p):
                    os.unlink(p)


# ═══════════════════════════════════════════════════════════════════════════
# 7. tools/cad_tool.py
# ═══════════════════════════════════════════════════════════════════════════
from tools.cad_tool import CADTool


class TestCADToolNoOpenSCAD(unittest.TestCase):
    def test_openscad_render_no_exe(self):
        ct = CADTool(openscad_path='/nonexistent')
        r = ct.openscad_render('cube([10,10,10]);', 'out.stl')
        self.assertFalse(r['ok'])

    def test_run_unknown_action(self):
        ct = CADTool()
        r = ct.run(action='unknown')
        self.assertFalse(r['ok'])


class TestCADToolEzdxf(unittest.TestCase):
    def test_create_and_read_dxf(self):
        try:
            import ezdxf  # noqa: F401
        except ImportError:
            self.skipTest('ezdxf not installed')

        ct = CADTool()
        with tempfile.NamedTemporaryFile(suffix='.dxf', delete=False) as f:
            path = f.name
        try:
            r = ct.create_dxf(path, [
                {'type': 'line', 'start': [0, 0], 'end': [100, 100]},
                {'type': 'circle', 'center': [50, 50], 'radius': 25},
                {'type': 'text', 'text': 'Hello', 'insert': [10, 10], 'height': 5},
            ])
            self.assertTrue(r['ok'])
            self.assertTrue(os.path.exists(path))

            r2 = ct.read_dxf(path)
            self.assertTrue(r2['ok'])
        finally:
            if os.path.exists(path):
                os.unlink(path)


# ═══════════════════════════════════════════════════════════════════════════
# 8. tools/upwork_tool.py
# ═══════════════════════════════════════════════════════════════════════════
from tools.upwork_tool import UpworkTool


class TestUpworkToolNoCredentials(unittest.TestCase):
    def test_not_ready(self):
        ut = UpworkTool()
        self.assertFalse(ut.is_ready)

    def test_search_no_creds(self):
        ut = UpworkTool()
        r = ut.search_jobs('python')
        self.assertIn('error', r)

    def test_run_unknown_action(self):
        ut = UpworkTool()
        r = ut.run(action='unknown')
        self.assertIn('error', r)


class TestUpworkToolWithCredentials(unittest.TestCase):
    @patch('urllib.request.urlopen')
    def test_search_jobs(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = json.dumps({
            'data': {'marketplaceJobPostings': {'totalCount': 0, 'edges': []}}
        }).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        ut = UpworkTool(client_id='cid', access_token='tok')
        r = ut.search_jobs('python')
        self.assertIn('jobs', r)


# ═══════════════════════════════════════════════════════════════════════════
# 9. perception/speech_synthesizer.py
# ═══════════════════════════════════════════════════════════════════════════
from perception.speech_synthesizer import SpeechSynthesizer


class TestSpeechSynthesizerNoClient(unittest.TestCase):
    def test_no_client(self):
        ss = SpeechSynthesizer()
        r = ss.synthesize('Hello world')
        self.assertIsNone(r)

    def test_voices_constant(self):
        self.assertIn('nova', SpeechSynthesizer.VOICES)
        self.assertIn('alloy', SpeechSynthesizer.VOICES)

    def test_cleanup_nonexistent(self):
        ss = SpeechSynthesizer()
        # Should not raise on missing file
        ss.cleanup('/nonexistent/file.opus')


class TestSpeechSynthesizerWithClient(unittest.TestCase):
    def test_synthesize_success(self):
        mock_client = MagicMock()
        mock_audio = MagicMock()
        mock_audio.content = b'\x00\x01\x02'  # fake audio bytes
        mock_client._client.audio.speech.create.return_value = mock_audio

        ss = SpeechSynthesizer(openai_client=mock_client)
        with tempfile.TemporaryDirectory() as td:
            with patch('tempfile.NamedTemporaryFile') as mock_tmp:
                tmp_file = MagicMock()
                tmp_file.name = os.path.join(td, 'test.opus')
                tmp_file.__enter__ = MagicMock(return_value=tmp_file)
                tmp_file.__exit__ = MagicMock(return_value=False)
                mock_tmp.return_value = tmp_file

                ss.synthesize('Hello')

    def test_text_truncation(self):
        mock_client = MagicMock()
        mock_audio = MagicMock()
        mock_audio.content = b'audio'
        mock_client._client.audio.speech.create.return_value = mock_audio

        ss = SpeechSynthesizer(openai_client=mock_client)
        long_text = 'A' * 5000
        ss.synthesize(long_text)
        call_args = mock_client._client.audio.speech.create.call_args
        # Should truncate to 4096
        actual_input = call_args.kwargs.get('input', call_args[1].get('input', ''))
        self.assertLessEqual(len(actual_input), 4096)


if __name__ == '__main__':
    unittest.main()
