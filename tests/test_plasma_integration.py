import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ezdxf


def _make_square_dxf_bytes() -> bytes:
    """Build a minimal DXF with a 2x2 closed square and return as bytes."""
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    msp.add_lwpolyline([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)], close=True)
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode('utf-8')


class TestPlasmaFlaskRoute(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()

        import dumbcam_gui_app as _app_module
        _app_module.UPLOAD_FOLDER = self._tmpdir
        _app_module.OUTPUT_FOLDER = self._tmpdir
        _app_module.app.config['TESTING'] = True
        _app_module.app.config['RATELIMIT_ENABLED'] = False
        _app_module.app.config['SECRET_KEY'] = 'test-secret-key'
        # Reset rate limiter storage so tests don't exceed per-minute limits
        _app_module.limiter.reset()
        self.client = _app_module.app.test_client()
        self._dxf = _make_square_dxf_bytes()

    def _post_plasma(self, **overrides):
        data = {
            'file': (io.BytesIO(self._dxf), 'test.dxf'),
            'machine_mode': 'plasma',
            'plasma_feed_rate': '129',
            'plasma_pierce_height': '0.15',
            'plasma_cut_height': '0.063',
            'plasma_pierce_delay': '0.5',
            'plasma_first_pierce_time': '0.0',
            'plasma_plunge_rate': '100',
            'plasma_end_delay': '0.0',
            'plasma_retract_height': '1.0',
            'plasma_ihs_springback': '0.02',
        }
        data.update(overrides)
        return self.client.post('/process', data=data,
                                content_type='multipart/form-data')

    def test_success_returns_200(self):
        r = self._post_plasma()
        self.assertEqual(r.status_code, 200)

    def test_response_success_flag(self):
        r = self._post_plasma()
        self.assertTrue(r.get_json()['success'])

    def test_response_has_gcode(self):
        r = self._post_plasma()
        self.assertIn('gcode', r.get_json())

    def test_response_has_mode_plasma(self):
        r = self._post_plasma()
        self.assertEqual(r.get_json()['mode'], 'plasma')

    def test_gcode_starts_with_version_comment(self):
        r = self._post_plasma()
        gcode = r.get_json()['gcode']
        self.assertTrue(gcode.startswith('(v1.6-sc)'),
                        f'First line was: {gcode.split(chr(10))[0]}')

    def test_gcode_ends_with_ps_comment(self):
        import re
        r = self._post_plasma()
        last = r.get_json()['gcode'].strip().split('\n')[-1]
        self.assertRegex(last, r'^\(PS\d+\)$')

    def test_console_format(self):
        r = self._post_plasma()
        console = r.get_json()['console']
        self.assertIn('Plasma:', console)
        self.assertIn('cut loop', console)
        self.assertIn('Total lines:', console)

    def test_filename_token_in_response(self):
        r = self._post_plasma()
        data = r.get_json()
        self.assertIn('filename', data)
        self.assertIsInstance(data['filename'], str)
        self.assertGreater(len(data['filename']), 0)

    def test_no_dxf_loops_returns_400(self):
        """A DXF with only an open polyline yields no loops → 400."""
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()
        msp.add_lwpolyline([(0,0),(1,0),(1,1)])  # open, not closed
        buf = io.StringIO()
        doc.write(buf)
        empty_dxf = buf.getvalue().encode('utf-8')
        r = self._post_plasma(file=(io.BytesIO(empty_dxf), 'open.dxf'))
        self.assertEqual(r.status_code, 400)
        self.assertIn('No closed cut loops', r.get_json()['error'])

    def test_invalid_float_param_returns_400(self):
        r = self._post_plasma(plasma_feed_rate='not_a_number')
        self.assertEqual(r.status_code, 400)
        self.assertIn('Invalid plasma parameter', r.get_json()['error'])

    def test_missing_file_returns_400(self):
        r = self.client.post('/process',
                             data={'machine_mode': 'plasma'},
                             content_type='multipart/form-data')
        self.assertEqual(r.status_code, 400)

    def test_gcode_is_ascii_only(self):
        r = self._post_plasma()
        gcode = r.get_json()['gcode']
        gcode.encode('ascii')  # raises UnicodeEncodeError if unicode present

    def test_gcode_has_no_nested_comments(self):
        r = self._post_plasma()
        for line in r.get_json()['gcode'].split('\n'):
            depth = 0
            for ch in line:
                if ch == '(':
                    depth += 1
                    self.assertLessEqual(depth, 1, f'Nested comment: {line}')
                elif ch == ')':
                    depth -= 1

    def test_lead_length_changes_output(self):
        no_lead = self._post_plasma(plasma_lead_length='0').get_json()['gcode']
        with_lead = self._post_plasma(
            plasma_lead_length='0.25').get_json()['gcode']
        self.assertNotEqual(no_lead, with_lead)

    def test_lead_points_accepted(self):
        r = self._post_plasma(plasma_lead_length='0.25',
                              plasma_lead_points='[[2.0, 2.0]]')
        self.assertEqual(r.status_code, 200)

    def test_invalid_lead_points_rejected(self):
        r = self._post_plasma(plasma_lead_length='0.25',
                              plasma_lead_points='not-json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('Invalid plasma parameter', r.get_json()['error'])
