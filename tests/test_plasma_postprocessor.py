import unittest
import sys, os
import math
import re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plasma_postprocessor import (
    PlasmaConfig, PlasmaPostProcessor, format_num, sanitize_comment,
    dxf_to_polylines, polylines_to_loops, normalize_to_origin,
    _point_in_polygon, _polygon_centroid, _classify_loops,
    _rotate_vec, _compute_v_lead,
    _nearest_vertex_index, build_loops_with_leads,
)


class TestPlasmaConfig(unittest.TestCase):

    def test_defaults(self):
        cfg = PlasmaConfig()
        self.assertEqual(cfg.pierce_height, 0.15)
        self.assertEqual(cfg.cut_height, 0.063)
        self.assertEqual(cfg.pierce_delay, 0.5)
        self.assertEqual(cfg.first_pierce_time, 0.0)
        self.assertEqual(cfg.plunge_rate, 100.0)
        self.assertEqual(cfg.end_delay, 0.0)
        self.assertEqual(cfg.retract_height, 1.0)
        self.assertEqual(cfg.ihs_springback, 0.020)
        self.assertEqual(cfg.units, 'inch')

    def test_custom_values(self):
        cfg = PlasmaConfig(pierce_height=0.20, cut_height=0.08, units='mm')
        self.assertEqual(cfg.pierce_height, 0.20)
        self.assertEqual(cfg.units, 'mm')


class TestFormatNum(unittest.TestCase):

    def test_zero_has_decimal(self):
        self.assertEqual(format_num(0), '0.0')

    def test_whole_number_has_decimal(self):
        self.assertEqual(format_num(100.0), '100.0')

    def test_max_four_decimals(self):
        self.assertEqual(format_num(0.12345678), '0.1235')

    def test_trailing_zeros_stripped(self):
        self.assertEqual(format_num(0.1500), '0.15')

    def test_negative(self):
        self.assertEqual(format_num(-5.0), '-5.0')

    def test_small_value(self):
        self.assertEqual(format_num(0.02), '0.02')

    def test_four_significant_decimals(self):
        self.assertEqual(format_num(0.0625), '0.0625')


class TestSanitizeComment(unittest.TestCase):

    def test_strips_parens(self):
        self.assertEqual(sanitize_comment('hello (world)'), 'hello world')

    def test_leaves_safe_text_alone(self):
        self.assertEqual(sanitize_comment('Pierce Height'), 'Pierce Height')

    def test_multiple_parens(self):
        self.assertEqual(sanitize_comment('(a) and (b)'), 'a and b')


class TestPreamble(unittest.TestCase):

    def test_first_line_is_version_comment(self):
        pp = PlasmaPostProcessor()
        cfg = PlasmaConfig()
        lines = pp._emit_preamble(cfg)
        self.assertEqual(lines[0], '(v1.6-sc)')

    def test_preamble_inch_sequence(self):
        pp = PlasmaPostProcessor()
        cfg = PlasmaConfig(units='inch')
        lines = pp._emit_preamble(cfg)
        self.assertEqual(lines, ['(v1.6-sc)', 'G90 G94', 'G17', 'G20', 'H0'])

    def test_preamble_metric_sequence(self):
        pp = PlasmaPostProcessor()
        cfg = PlasmaConfig(units='mm')
        lines = pp._emit_preamble(cfg)
        self.assertEqual(lines, ['(v1.6-sc)', 'G90 G94', 'G17', 'G21', 'H0'])


class TestPostamble(unittest.TestCase):

    def test_second_to_last_is_m5_m30(self):
        pp = PlasmaPostProcessor()
        lines = pp._emit_postamble(129)
        self.assertEqual(lines[-2], 'M5 M30')

    def test_last_line_is_ps_comment(self):
        pp = PlasmaPostProcessor()
        lines = pp._emit_postamble(129)
        self.assertEqual(lines[-1], '(PS129)')

    def test_program_speed_is_integer(self):
        pp = PlasmaPostProcessor()
        lines = pp._emit_postamble(129.7)
        self.assertEqual(lines[-1], '(PS129)')


class TestIHSActive(unittest.TestCase):

    def test_active_when_both_nonzero(self):
        pp = PlasmaPostProcessor()
        cfg = PlasmaConfig(pierce_height=0.15, cut_height=0.063)
        self.assertTrue(pp._ihs_active(cfg))

    def test_inactive_when_pierce_height_zero(self):
        pp = PlasmaPostProcessor()
        cfg = PlasmaConfig(pierce_height=0.0, cut_height=0.063)
        self.assertFalse(pp._ihs_active(cfg))

    def test_inactive_when_cut_height_zero(self):
        pp = PlasmaPostProcessor()
        cfg = PlasmaConfig(pierce_height=0.15, cut_height=0.0)
        self.assertFalse(pp._ihs_active(cfg))


class TestPenDown(unittest.TestCase):

    def setUp(self):
        self.pp = PlasmaPostProcessor()
        self.cfg = PlasmaConfig(
            pierce_height=0.15,
            cut_height=0.063,
            pierce_delay=0.5,
            first_pierce_time=0.0,
            plunge_rate=100.0,
            ihs_springback=0.0,
            units='inch',
        )

    def test_ihs_sequence_starts_with_blank_line(self):
        lines = self.pp._emit_pen_down(self.cfg, is_first_pierce=False)
        self.assertEqual(lines[0], '')

    def test_ihs_sequence_order(self):
        lines = self.pp._emit_pen_down(self.cfg, is_first_pierce=False)
        # Remove blank line
        seq = [l for l in lines if l]
        self.assertEqual(seq[0], 'G92 Z0.0')
        self.assertIn('G38.2', seq[1])
        self.assertIn('G38.4', seq[2])
        self.assertEqual(seq[3], 'G92 Z0.0')
        self.assertIn('IHS Backlash', seq[4])
        self.assertEqual(seq[5], 'G92 Z0.0')
        self.assertIn('Pierce Height', seq[6])
        self.assertEqual(seq[7], 'M3')
        self.assertIn('G4', seq[8])
        self.assertIn('Cut Height', seq[9])
        self.assertEqual(seq[10], 'H1')

    def test_backlash_lift_includes_springback(self):
        cfg = PlasmaConfig(pierce_height=0.15, cut_height=0.063,
                           ihs_springback=0.020)
        lines = self.pp._emit_pen_down(cfg, is_first_pierce=False)
        backlash_line = next(l for l in lines if 'IHS Backlash' in l)
        # 0.020 springback + 0.02 mechanical = 0.04
        self.assertIn('Z0.04', backlash_line)

    def test_cut_height_uses_g1_not_g0(self):
        lines = self.pp._emit_pen_down(self.cfg, is_first_pierce=False)
        cut_height_line = next(l for l in lines if 'Cut Height' in l)
        self.assertTrue(cut_height_line.startswith('G1'))

    def test_pierce_dwell_emitted_when_nonzero(self):
        lines = self.pp._emit_pen_down(self.cfg, is_first_pierce=False)
        dwell_lines = [l for l in lines if l.startswith('G4')]
        self.assertEqual(len(dwell_lines), 1)
        self.assertIn('P0.5', dwell_lines[0])

    def test_pierce_dwell_skipped_when_zero(self):
        cfg = PlasmaConfig(pierce_height=0.15, cut_height=0.063, pierce_delay=0.0)
        lines = self.pp._emit_pen_down(cfg, is_first_pierce=False)
        dwell_lines = [l for l in lines if l.startswith('G4')]
        self.assertEqual(len(dwell_lines), 0)

    def test_first_pierce_time_added_on_first_loop(self):
        cfg = PlasmaConfig(pierce_height=0.15, cut_height=0.063,
                           pierce_delay=0.5, first_pierce_time=0.3)
        lines = self.pp._emit_pen_down(cfg, is_first_pierce=True)
        dwell_line = next(l for l in lines if l.startswith('G4'))
        self.assertIn('P0.8', dwell_line)

    def test_no_ihs_collapses_to_m3_and_dwell(self):
        cfg = PlasmaConfig(pierce_height=0.0, cut_height=0.0, pierce_delay=0.5)
        lines = self.pp._emit_pen_down(cfg, is_first_pierce=False)
        non_blank = [l for l in lines if l]
        self.assertEqual(non_blank[0], 'M3')
        self.assertTrue(non_blank[1].startswith('G4'))
        # No Z words, no H1
        for l in non_blank:
            self.assertNotIn('Z', l)
            self.assertNotIn('H1', l)

    def test_probe_seek_down_uses_hardcoded_values_inch(self):
        lines = self.pp._emit_pen_down(self.cfg, is_first_pierce=False)
        seek_down = next(l for l in lines if 'G38.2' in l)
        self.assertIn('Z-5.0', seek_down)
        self.assertIn('F100.0', seek_down)

    def test_probe_seek_up_uses_hardcoded_values_inch(self):
        lines = self.pp._emit_pen_down(self.cfg, is_first_pierce=False)
        seek_up = next(l for l in lines if 'G38.4' in l)
        self.assertIn('Z0.5', seek_up)
        self.assertIn('F20.0', seek_up)

    def test_metric_probe_values_scaled(self):
        cfg = PlasmaConfig(pierce_height=3.81, cut_height=1.6,
                           plunge_rate=2540.0, units='mm')
        lines = self.pp._emit_pen_down(cfg, is_first_pierce=False)
        seek_down = next(l for l in lines if 'G38.2' in l)
        # -5 * 25.4 = -127.0
        self.assertIn('Z-127.0', seek_down)
        # 100 * 25.4 = 2540.0
        self.assertIn('F2540.0', seek_down)


class TestPenUp(unittest.TestCase):

    def setUp(self):
        self.pp = PlasmaPostProcessor()

    def test_h0_before_m5(self):
        cfg = PlasmaConfig(pierce_height=0.15, cut_height=0.063)
        lines = self.pp._emit_pen_up(cfg)
        h0_idx = lines.index('H0')
        m5_idx = lines.index('M5')
        self.assertLess(h0_idx, m5_idx)

    def test_retract_emitted_when_ihs_active(self):
        cfg = PlasmaConfig(pierce_height=0.15, cut_height=0.063, retract_height=1.0)
        lines = self.pp._emit_pen_up(cfg)
        retract_lines = [l for l in lines if 'Z' in l and 'G0' in l]
        self.assertEqual(len(retract_lines), 1)
        self.assertIn('Z1.0', retract_lines[0])

    def test_no_retract_when_ihs_inactive(self):
        cfg = PlasmaConfig(pierce_height=0.0, cut_height=0.0)
        lines = self.pp._emit_pen_up(cfg)
        z_lines = [l for l in lines if 'Z' in l]
        self.assertEqual(len(z_lines), 0)

    def test_end_delay_emitted_when_nonzero(self):
        cfg = PlasmaConfig(pierce_height=0.15, cut_height=0.063, end_delay=0.3)
        lines = self.pp._emit_pen_up(cfg)
        dwell_lines = [l for l in lines if l.startswith('G4')]
        self.assertEqual(len(dwell_lines), 1)
        self.assertIn('P0.3', dwell_lines[0])

    def test_end_delay_skipped_when_zero(self):
        cfg = PlasmaConfig(pierce_height=0.15, cut_height=0.063, end_delay=0.0)
        lines = self.pp._emit_pen_up(cfg)
        dwell_lines = [l for l in lines if l.startswith('G4')]
        self.assertEqual(len(dwell_lines), 0)

    def test_end_delay_before_retract(self):
        cfg = PlasmaConfig(pierce_height=0.15, cut_height=0.063,
                           end_delay=0.2, retract_height=1.0)
        lines = self.pp._emit_pen_up(cfg)
        dwell_idx = next(i for i, l in enumerate(lines) if l.startswith('G4'))
        retract_idx = next(i for i, l in enumerate(lines) if 'Z' in l and 'G0' in l)
        self.assertLess(dwell_idx, retract_idx)

    def test_metric_retract_scaled(self):
        cfg = PlasmaConfig(pierce_height=3.81, cut_height=1.6,
                           retract_height=1.0, units='mm')
        lines = self.pp._emit_pen_up(cfg)
        retract_lines = [l for l in lines if 'Z' in l and 'G0' in l]
        # 1.0 * 25.4 = 25.4
        self.assertIn('Z25.4', retract_lines[0])


class TestEmitRapid(unittest.TestCase):

    def setUp(self):
        self.pp = PlasmaPostProcessor()

    def test_basic_rapid(self):
        lines = self.pp._emit_rapid(1.0, 2.0, 0.0, 0.0)
        self.assertEqual(lines, ['G0 X1.0 Y2.0'])

    def test_suppressed_when_distance_too_small(self):
        lines = self.pp._emit_rapid(0.0001, 0.0, 0.0, 0.0)
        self.assertEqual(lines, [])

    def test_modal_x_suppressed_when_unchanged(self):
        lines = self.pp._emit_rapid(1.0, 2.0, 1.0, 0.0)
        self.assertNotIn('X', lines[0])
        self.assertIn('Y2.0', lines[0])

    def test_modal_y_suppressed_when_unchanged(self):
        lines = self.pp._emit_rapid(1.0, 2.0, 0.0, 2.0)
        self.assertIn('X1.0', lines[0])
        self.assertNotIn(' Y', lines[0])

    def test_no_z_or_f(self):
        lines = self.pp._emit_rapid(1.0, 2.0, 0.0, 0.0)
        self.assertNotIn('Z', lines[0])
        self.assertNotIn('F', lines[0])


class TestEmitLinear(unittest.TestCase):

    def setUp(self):
        self.pp = PlasmaPostProcessor()

    def test_basic_linear(self):
        lines = self.pp._emit_linear(3.0, 4.0, 129.0, 0.0, 0.0, None)
        self.assertEqual(lines, ['G1 X3.0 Y4.0 F129.0'])

    def test_modal_f_suppressed_when_unchanged(self):
        lines = self.pp._emit_linear(3.0, 4.0, 129.0, 0.0, 0.0, 129.0)
        self.assertNotIn('F', lines[0])

    def test_modal_x_suppressed_when_unchanged(self):
        lines = self.pp._emit_linear(3.0, 4.0, 129.0, 3.0, 0.0, None)
        self.assertNotIn('X', lines[0])
        self.assertIn('Y4.0', lines[0])

    def test_empty_when_no_xy_change(self):
        lines = self.pp._emit_linear(3.0, 4.0, 129.0, 3.0, 4.0, None)
        self.assertEqual(lines, [])


class TestEmitArc(unittest.TestCase):

    def setUp(self):
        self.pp = PlasmaPostProcessor()

    def test_cw_arc_uses_g2(self):
        lines = self.pp._emit_arc(1.0, 0.0, 0.5, 0.0, True, 100.0, 0.0, 0.0, None)
        self.assertTrue(lines[0].startswith('G2'))

    def test_ccw_arc_uses_g3(self):
        lines = self.pp._emit_arc(1.0, 0.0, 0.5, 0.0, False, 100.0, 0.0, 0.0, None)
        self.assertTrue(lines[0].startswith('G3'))

    def test_ij_are_incremental(self):
        # Arc from (0,0) to (1,0), center at (0.5,0)
        # I = center_x - cur_x = 0.5 - 0.0 = 0.5
        # J = center_y - cur_y = 0.0 - 0.0 = 0.0
        lines = self.pp._emit_arc(1.0, 0.0, 0.5, 0.0, True, 100.0, 0.0, 0.0, None)
        self.assertIn('I0.5', lines[0])
        self.assertIn('J0.0', lines[0])

    def test_no_k_or_z_on_arc(self):
        lines = self.pp._emit_arc(1.0, 0.0, 0.5, 0.0, True, 100.0, 0.0, 0.0, None)
        self.assertNotIn('K', lines[0])
        self.assertNotIn('Z', lines[0])

    def test_tiny_arc_demoted_to_linear(self):
        # Radius 0.04" < 0.05" threshold → should emit G1 instead
        # Arc from (0,0) to (0.04,0), center at (0.02,0), radius=0.02
        lines = self.pp._emit_arc(0.04, 0.0, 0.02, 0.0, True, 100.0, 0.0, 0.0, None)
        self.assertTrue(lines[0].startswith('G1'))
        self.assertNotIn('I', lines[0])


class TestCircleSplitter(unittest.TestCase):

    def setUp(self):
        self.pp = PlasmaPostProcessor()

    def test_full_circle_split_into_two_arcs(self):
        # Full circle: start at (1,0), center at (0,0), radius=1
        lines = self.pp._split_full_circle(0.0, 0.0, 1.0, True, 100.0, 1.0, 0.0)
        arc_lines = [l for l in lines if l.startswith('G2') or l.startswith('G3')]
        self.assertEqual(len(arc_lines), 2)

    def test_circle_ends_at_start_point(self):
        # Two arcs should together bring tool back to start
        # First arc ends at antipodal (-1, 0), second arc ends at start (1, 0)
        lines = self.pp._split_full_circle(0.0, 0.0, 1.0, True, 100.0, 1.0, 0.0)
        # Second arc endpoint should be start point X1.0
        self.assertIn('X1.0', lines[-1])


class TestGenerateGcode(unittest.TestCase):

    def _make_rect_loop(self, feed=129.0):
        """Rectangular loop matching §8 worked example (approximate coords)."""
        return {
            'start_x': 0.6941, 'start_y': 5.5301,
            'segments': [
                {'type': 'line', 'x': 3.4441, 'y': 5.5301, 'feed': feed},
                {'type': 'line', 'x': 3.4441, 'y': 7.2801, 'feed': feed},
                {'type': 'line', 'x': 0.6941, 'y': 7.2801, 'feed': feed},
                {'type': 'line', 'x': 0.6941, 'y': 5.5301, 'feed': feed},
            ]
        }

    def test_returns_string(self):
        pp = PlasmaPostProcessor()
        cfg = PlasmaConfig()
        result = pp.generate_gcode([self._make_rect_loop()], cfg)
        self.assertIsInstance(result, str)

    def test_first_line_is_version_comment(self):
        pp = PlasmaPostProcessor()
        cfg = PlasmaConfig()
        result = pp.generate_gcode([self._make_rect_loop()], cfg)
        self.assertEqual(result.split('\n')[0], '(v1.6-sc)')

    def test_last_line_is_ps_comment(self):
        pp = PlasmaPostProcessor()
        cfg = PlasmaConfig()
        result = pp.generate_gcode([self._make_rect_loop()], cfg)
        last = result.split('\n')[-1]
        self.assertRegex(last, r'^\(PS\d+\)$')

    def test_program_speed_is_max_cut_feed(self):
        pp = PlasmaPostProcessor()
        cfg = PlasmaConfig()
        result = pp.generate_gcode([self._make_rect_loop(feed=129.0)], cfg)
        self.assertIn('(PS129)', result)

    def test_m3_and_m5_balanced(self):
        pp = PlasmaPostProcessor()
        cfg = PlasmaConfig()
        result = pp.generate_gcode([self._make_rect_loop(), self._make_rect_loop()], cfg)
        lines = result.split('\n')
        m3_count = sum(1 for l in lines if l.strip() == 'M3')
        m5_count = sum(1 for l in lines if l.strip() in ('M5', 'M5 M30'))
        # One M3 per loop + 1 M5 per loop (pen-up) + 1 M5 in postamble
        self.assertEqual(m3_count, 2)

    def test_no_z_in_cut_moves(self):
        pp = PlasmaPostProcessor()
        cfg = PlasmaConfig()
        result = pp.generate_gcode([self._make_rect_loop()], cfg)
        lines = result.split('\n')
        in_cut = False
        for line in lines:
            if line.strip() == 'H1':
                in_cut = True
            if line.strip() == 'H0':
                in_cut = False
            if in_cut and 'Z' in line:
                self.fail(f'Z found in cut moves: {line}')

    def _first_cut_move_of_each_loop(self, result):
        """Return the first G1/G2/G3 line after each H1 (the loop's entry move)."""
        entries = []
        lines = [l.strip() for l in result.split('\n')]
        i = 0
        while i < len(lines):
            if lines[i] == 'H1':
                for j in range(i + 1, len(lines)):
                    if lines[j].startswith(('G1', 'G2', 'G3')):
                        entries.append(lines[j])
                        break
            i += 1
        return entries

    def test_first_cut_move_emits_both_x_and_y(self):
        # FireControl cancels modal X/Y after each pen-up (spec lines 206-207,
        # CancelModalNumbers). The first cut move of every loop must therefore
        # re-establish absolute position with BOTH X and Y words; suppressing
        # the axis that matches start_x/start_y produces a malformed entry move.
        pp = PlasmaPostProcessor()
        cfg = PlasmaConfig()
        # Two loops so we exercise both the first loop and a subsequent one.
        result = pp.generate_gcode(
            [self._make_rect_loop(), self._make_rect_loop()], cfg)
        entries = self._first_cut_move_of_each_loop(result)
        self.assertEqual(len(entries), 2)
        for entry in entries:
            self.assertIn('X', entry, f'first cut move missing X: {entry}')
            self.assertIn('Y', entry, f'first cut move missing Y: {entry}')

    def test_rapids_to_first_loop_start_before_piercing(self):
        # The machine starts at work zero (0,0). Without a rapid to the first
        # loop's start, the torch pierces at the origin and the first cut move
        # drags a diagonal from (0,0) into the part instead of tracing the
        # contour. The first motion of the program must be a G0 to the loop
        # start, emitted before the first M3.
        pp = PlasmaPostProcessor()
        cfg = PlasmaConfig()
        loop = self._make_rect_loop()
        result = pp.generate_gcode([loop], cfg)
        lines = [l.strip() for l in result.split('\n')]
        first_motion = next(l for l in lines
                            if l.startswith(('G0 ', 'G1 ', 'G2 ', 'G3 ')))
        self.assertTrue(first_motion.startswith('G0 '),
                        f'first motion is not a rapid: {first_motion}')
        self.assertIn(f'X{format_num(loop["start_x"])}', first_motion)
        self.assertIn(f'Y{format_num(loop["start_y"])}', first_motion)
        # And the rapid must come before the first torch-on.
        self.assertLess(lines.index(first_motion), lines.index('M3'))

    def test_no_unicode(self):
        pp = PlasmaPostProcessor()
        cfg = PlasmaConfig()
        result = pp.generate_gcode([self._make_rect_loop()], cfg)
        result.encode('ascii')  # raises UnicodeEncodeError if unicode present

    def test_no_nested_comments(self):
        pp = PlasmaPostProcessor()
        cfg = PlasmaConfig()
        result = pp.generate_gcode([self._make_rect_loop()], cfg)
        for line in result.split('\n'):
            depth = 0
            for ch in line:
                if ch == '(':
                    depth += 1
                    self.assertLessEqual(depth, 1, f'Nested comment in: {line}')
                elif ch == ')':
                    depth -= 1


import os
import tempfile
import ezdxf


class TestNormalizeToOrigin(unittest.TestCase):

    def test_translates_min_corner_to_origin(self):
        polys = [[(-2.0, -1.0), (0.0, -1.0), (0.0, 1.0), (-2.0, 1.0)]]
        out = normalize_to_origin(polys)
        xs = [x for p in out for x, y in p]
        ys = [y for p in out for x, y in p]
        self.assertAlmostEqual(min(xs), 0.0)
        self.assertAlmostEqual(min(ys), 0.0)

    def test_preserves_dimensions(self):
        polys = [[(-2.0, -1.0), (0.0, -1.0), (0.0, 1.0), (-2.0, 1.0)]]
        out = normalize_to_origin(polys)
        xs = [x for p in out for x, y in p]
        ys = [y for p in out for x, y in p]
        self.assertAlmostEqual(max(xs) - min(xs), 2.0)
        self.assertAlmostEqual(max(ys) - min(ys), 2.0)

    def test_already_at_origin_unchanged(self):
        polys = [[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]]
        out = normalize_to_origin(polys)
        self.assertEqual(out, polys)

    def test_multiple_polylines_share_a_single_global_offset(self):
        # Two loops; relative spacing between them must be preserved.
        polys = [
            [(-4.0, -2.0), (-3.0, -2.0), (-3.0, -1.0), (-4.0, -1.0)],
            [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
        ]
        out = normalize_to_origin(polys)
        # Global min was (-4, -2); second loop shifts by (+4, +2).
        self.assertAlmostEqual(out[1][0][0], 4.0)
        self.assertAlmostEqual(out[1][0][1], 2.0)

    def test_empty_returns_empty(self):
        self.assertEqual(normalize_to_origin([]), [])


class TestDxfToPolylines(unittest.TestCase):

    def _make_dxf(self, setup_fn) -> str:
        """Write a temp DXF file. setup_fn receives the modelspace. Returns file path."""
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()
        setup_fn(msp)
        with tempfile.NamedTemporaryFile(suffix='.dxf', delete=False) as f:
            tmp_path = f.name
        doc.saveas(tmp_path)
        self.addCleanup(os.unlink, tmp_path)
        return tmp_path

    def test_closed_lwpolyline_returned(self):
        path = self._make_dxf(lambda msp:
            msp.add_lwpolyline([(0,0),(2,0),(2,2),(0,2)], close=True))
        result = dxf_to_polylines(path)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]), 4)

    def test_implicit_close_by_repeated_first_point(self):
        """LWPOLYLINE where last point matches first within 0.001 is treated as closed."""
        path = self._make_dxf(lambda msp:
            msp.add_lwpolyline([(0,0),(2,0),(2,2),(0,2),(0,0)]))
        result = dxf_to_polylines(path)
        self.assertEqual(len(result), 1)

    def test_open_lwpolyline_skipped(self):
        path = self._make_dxf(lambda msp:
            msp.add_lwpolyline([(0,0),(2,0),(2,2)]))  # not closed
        result = dxf_to_polylines(path)
        self.assertEqual(len(result), 0)

    def test_circle_becomes_32_point_polygon(self):
        path = self._make_dxf(lambda msp: msp.add_circle((5,5), radius=1.0))
        result = dxf_to_polylines(path)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]), 32)

    def test_circle_points_lie_on_circle(self):
        path = self._make_dxf(lambda msp: msp.add_circle((0,0), radius=1.0))
        result = dxf_to_polylines(path)
        for x, y in result[0]:
            self.assertAlmostEqual(math.hypot(x, y), 1.0, places=4)

    def test_line_entities_chained_into_closed_loop(self):
        def setup(msp):
            msp.add_line((0,0), (2,0))
            msp.add_line((2,0), (2,2))
            msp.add_line((2,2), (0,2))
            msp.add_line((0,2), (0,0))
        path = self._make_dxf(setup)
        result = dxf_to_polylines(path)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]), 4)

    def test_open_line_chain_skipped(self):
        def setup(msp):
            msp.add_line((0,0), (2,0))
            msp.add_line((2,0), (2,2))
            # intentionally not closed
        path = self._make_dxf(setup)
        result = dxf_to_polylines(path)
        self.assertEqual(len(result), 0)

    def test_standalone_open_arc_does_not_close(self):
        # A single open arc cannot form a closed loop on its own.
        path = self._make_dxf(lambda msp:
            msp.add_arc((0,0), radius=1.0, start_angle=0, end_angle=270))
        result = dxf_to_polylines(path)
        self.assertEqual(len(result), 0)

    def test_lines_and_arcs_chain_into_closed_loop(self):
        # A "stadium" slot: two straight sides closed by two semicircular ends.
        # Lines and arcs must chain together into one closed loop, or filleted
        # / rounded parts (LINE+ARC outlines) can never be cut.
        def setup(msp):
            msp.add_line((0, 0), (4, 0))                                  # bottom
            msp.add_arc((4, 1), radius=1.0, start_angle=270, end_angle=90)  # right end
            msp.add_line((4, 2), (0, 2))                                  # top
            msp.add_arc((0, 1), radius=1.0, start_angle=90, end_angle=270)  # left end
        path = self._make_dxf(setup)
        result = dxf_to_polylines(path)
        self.assertEqual(len(result), 1)
        loop = result[0]
        # Tessellated arcs -> many points (well more than the 2 line endpoints).
        self.assertGreater(len(loop), 10)
        # Every point lies within the stadium's bounds.
        for x, y in loop:
            self.assertGreaterEqual(x, -1.001)
            self.assertLessEqual(x, 5.001)
            self.assertGreaterEqual(y, -0.001)
            self.assertLessEqual(y, 2.001)

    def test_empty_dxf_returns_empty_list(self):
        path = self._make_dxf(lambda msp: None)
        result = dxf_to_polylines(path)
        self.assertEqual(result, [])

    def test_multiple_shapes_all_returned(self):
        def setup(msp):
            msp.add_lwpolyline([(0,0),(2,0),(2,2),(0,2)], close=True)
            msp.add_lwpolyline([(5,5),(7,5),(7,7),(5,7)], close=True)
        path = self._make_dxf(setup)
        result = dxf_to_polylines(path)
        self.assertEqual(len(result), 2)

    def test_lines_chained_in_arbitrary_order(self):
        """Lines added in random order still chain into one loop."""
        def setup(msp):
            msp.add_line((2,2), (0,2))  # added last in the square
            msp.add_line((0,0), (2,0))
            msp.add_line((0,2), (0,0))
            msp.add_line((2,0), (2,2))
        path = self._make_dxf(setup)
        result = dxf_to_polylines(path)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]), 4)


class TestPolylinesToLoops(unittest.TestCase):

    def test_basic_square(self):
        points = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
        loops = polylines_to_loops([points], feed_rate=129.0)
        self.assertEqual(len(loops), 1)
        loop = loops[0]
        self.assertEqual(loop['start_x'], 0.0)
        self.assertEqual(loop['start_y'], 0.0)
        self.assertEqual(len(loop['segments']), 4)

    def test_segments_are_lines(self):
        points = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
        loops = polylines_to_loops([points], feed_rate=100.0)
        for seg in loops[0]['segments']:
            self.assertEqual(seg['type'], 'line')

    def test_closing_segment_returns_to_start(self):
        points = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
        loops = polylines_to_loops([points], feed_rate=100.0)
        last_seg = loops[0]['segments'][-1]
        self.assertEqual(last_seg['x'], 0.0)
        self.assertEqual(last_seg['y'], 0.0)

    def test_feed_rate_applied_to_all_segments(self):
        points = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
        loops = polylines_to_loops([points], feed_rate=75.0)
        for seg in loops[0]['segments']:
            self.assertEqual(seg['feed'], 75.0)

    def test_multiple_polylines(self):
        p1 = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
        p2 = [(5.0, 5.0), (6.0, 5.0), (6.0, 6.0)]
        loops = polylines_to_loops([p1, p2], feed_rate=100.0)
        self.assertEqual(len(loops), 2)
        self.assertEqual(loops[1]['start_x'], 5.0)


class TestAcceptance(unittest.TestCase):
    """Spec §9 acceptance tests against the §8 worked example."""

    def setUp(self):
        pp = PlasmaPostProcessor()
        cfg = PlasmaConfig(
            pierce_height=0.15,
            cut_height=0.063,
            pierce_delay=0.5,
            plunge_rate=100.0,
            ihs_springback=0.0,
            retract_height=1.0,
            units='inch',
        )
        rect = [(0.6941, 5.5301), (3.4441, 5.5301),
                (3.4441, 7.2801), (0.6941, 7.2801)]
        loops = polylines_to_loops([rect], feed_rate=129.0)
        self.gcode = pp.generate_gcode(loops, cfg)
        self.lines = self.gcode.split('\n')

    def test_first_line_version_comment(self):
        self.assertRegex(self.lines[0], r'^\(v1\.6-(sc|af)\)$')

    def test_last_line_ps_comment(self):
        self.assertRegex(self.lines[-1], r'^\(PS\d+(\.\d+)?\)$')

    def test_no_z_in_cut_moves(self):
        in_cut = False
        for line in self.lines:
            stripped = line.strip()
            if stripped == 'H1':
                in_cut = True
                continue
            if stripped == 'H0':
                in_cut = False
                continue
            if in_cut:
                self.assertNotIn('Z', stripped,
                    f'Z found in cut move section: {stripped}')

    def test_cut_moves_have_feed(self):
        in_cut = False
        first_move_in_loop = False
        for line in self.lines:
            stripped = line.strip()
            if stripped == 'H1':
                in_cut = True
                first_move_in_loop = True
                continue
            if stripped == 'H0':
                in_cut = False
                continue
            if in_cut and (stripped.startswith('G1') or stripped.startswith('G2')
                           or stripped.startswith('G3')):
                if first_move_in_loop:
                    self.assertIn('F', stripped,
                        f'First cut move in loop missing F: {stripped}')
                    first_move_in_loop = False

    def test_no_k_words(self):
        for line in self.lines:
            self.assertNotRegex(line, r'\bK[\d.-]', f'K word in: {line}')

    def test_no_z_on_arcs(self):
        arc_re = re.compile(r'^G[23] ')
        for line in self.lines:
            if arc_re.match(line):
                self.assertNotIn('Z', line, f'Z on arc: {line}')

    def test_m3_m5_pairing(self):
        torch_on = False
        for line in self.lines:
            stripped = line.strip()
            if stripped == 'M3':
                self.assertFalse(torch_on, 'M3 fired while torch already on')
                torch_on = True
            elif 'M5' in stripped:
                torch_on = False
        self.assertFalse(torch_on, 'Torch still on at end of program')

    def test_h1_preceded_by_m3(self):
        # M3 must appear somewhere before H1 in each pen-down sequence
        saw_m3 = False
        for line in self.lines:
            stripped = line.strip()
            if stripped == 'M3':
                saw_m3 = True
            elif stripped == 'H1':
                self.assertTrue(saw_m3, 'H1 appeared without prior M3 in sequence')
                saw_m3 = False
            elif stripped == 'H0':
                saw_m3 = False

    def test_no_more_than_four_decimals(self):
        coord_pattern = re.compile(r'[XYZIJF]-?[\d]+\.([\d]+)')
        for line in self.lines:
            for match in coord_pattern.finditer(line):
                decimals = match.group(1)
                self.assertLessEqual(len(decimals), 4,
                    f'More than 4 decimal places in: {line}')

    def test_no_standalone_full_circle_arcs(self):
        arc_re = re.compile(r'^G[23] ')
        cur_x, cur_y = 0.0, 0.0
        for line in self.lines:
            stripped = line.strip()
            if re.match(r'^G0 ', stripped) or re.match(r'^G1 ', stripped):
                x_match = re.search(r'X([-\d.]+)', stripped)
                y_match = re.search(r'Y([-\d.]+)', stripped)
                if x_match:
                    cur_x = float(x_match.group(1))
                if y_match:
                    cur_y = float(y_match.group(1))
            elif arc_re.match(stripped):
                x_match = re.search(r'X([-\d.]+)', stripped)
                y_match = re.search(r'Y([-\d.]+)', stripped)
                end_x = float(x_match.group(1)) if x_match else cur_x
                end_y = float(y_match.group(1)) if y_match else cur_y
                self.assertFalse(
                    abs(end_x - cur_x) < 0.0001 and abs(end_y - cur_y) < 0.0001,
                    f'Possible full-circle arc detected (start==end): {stripped}')
                cur_x, cur_y = end_x, end_y

    def test_output_is_ascii(self):
        self.gcode.encode('ascii')

    def test_no_nested_comments(self):
        for line in self.lines:
            depth = 0
            for ch in line:
                if ch == '(':
                    depth += 1
                    self.assertLessEqual(depth, 1, f'Nested comment: {line}')
                elif ch == ')':
                    depth -= 1


class TestLoopClassification(unittest.TestCase):

    def test_point_inside_square(self):
        sq = [(0, 0), (2, 0), (2, 2), (0, 2)]
        self.assertTrue(_point_in_polygon(1, 1, sq))

    def test_point_outside_square(self):
        sq = [(0, 0), (2, 0), (2, 2), (0, 2)]
        self.assertFalse(_point_in_polygon(3, 1, sq))

    def test_centroid_of_square(self):
        cx, cy = _polygon_centroid([(0, 0), (2, 0), (2, 2), (0, 2)])
        self.assertAlmostEqual(cx, 1.0)
        self.assertAlmostEqual(cy, 1.0)

    def test_perimeter_not_classified_as_hole(self):
        perimeter = [(0, 0), (10, 0), (10, 10), (0, 10)]
        hole = [(3, 3), (5, 3), (5, 5), (3, 5)]
        flags = _classify_loops([perimeter, hole])
        self.assertEqual(flags, [False, True])

    def test_single_loop_is_perimeter(self):
        self.assertEqual(_classify_loops([[(0, 0), (1, 0), (1, 1)]]), [False])

    def test_concentric_perimeter_not_hole(self):
        # The perimeter centroid (5,5) also lies inside the concentric hole,
        # so centroid-only containment would wrongly flag BOTH as holes.
        # The larger-area loop must remain the perimeter.
        perimeter = [(0, 0), (10, 0), (10, 10), (0, 10)]
        hole = [(3, 3), (7, 3), (7, 7), (3, 7)]
        self.assertEqual(_classify_loops([perimeter, hole]), [False, True])


class TestVLead(unittest.TestCase):

    SQUARE = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]

    def test_leg_lengths_equal_lead_length(self):
        v = _compute_v_lead(self.SQUARE, 0, scrap_outside=True, lead_length=0.25)
        cx, cy = v['C']
        din = math.hypot(v['P_in'][0] - cx, v['P_in'][1] - cy)
        dout = math.hypot(v['P_out'][0] - cx, v['P_out'][1] - cy)
        self.assertAlmostEqual(din, 0.25, places=4)
        self.assertAlmostEqual(dout, 0.25, places=4)

    def test_v_angle_is_60_degrees(self):
        v = _compute_v_lead(self.SQUARE, 0, scrap_outside=True, lead_length=0.25)
        cx, cy = v['C']
        ax, ay = v['P_in'][0] - cx, v['P_in'][1] - cy
        bx, by = v['P_out'][0] - cx, v['P_out'][1] - cy
        dot = ax * bx + ay * by
        cosang = dot / (math.hypot(ax, ay) * math.hypot(bx, by))
        self.assertAlmostEqual(math.degrees(math.acos(cosang)), 60.0, places=3)

    def test_perimeter_legs_outside(self):
        v = _compute_v_lead(self.SQUARE, 0, scrap_outside=True, lead_length=0.25)
        self.assertFalse(_point_in_polygon(*v['P_in'], self.SQUARE))
        self.assertFalse(_point_in_polygon(*v['P_out'], self.SQUARE))

    def test_hole_legs_inside(self):
        hole = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
        v = _compute_v_lead(hole, 0, scrap_outside=False, lead_length=0.25)
        self.assertTrue(_point_in_polygon(*v['P_in'], hole))
        self.assertTrue(_point_in_polygon(*v['P_out'], hole))

    def test_contour_starts_and_ends_at_C(self):
        v = _compute_v_lead(self.SQUARE, 2, scrap_outside=True, lead_length=0.25)
        self.assertEqual(v['contour'][0], v['C'])
        self.assertEqual(v['contour'][-1], v['C'])
        self.assertEqual(len(v['contour']), len(self.SQUARE) + 1)


class TestBuildLoopsWithLeads(unittest.TestCase):

    SQUARE = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]

    def test_zero_length_matches_plain_loops(self):
        plain = polylines_to_loops([self.SQUARE], feed_rate=100.0)
        leads = build_loops_with_leads(
            [self.SQUARE], feed_rate=100.0, lead_length=0.0, lead_points=[])
        self.assertEqual(leads, plain)

    def test_lead_moves_start_off_contour(self):
        loops = build_loops_with_leads(
            [self.SQUARE], feed_rate=100.0, lead_length=0.25, lead_points=[])
        loop = loops[0]
        self.assertNotIn((loop['start_x'], loop['start_y']),
                         [(x, y) for x, y in self.SQUARE])
        first = loop['segments'][0]
        self.assertAlmostEqual(first['x'], 0.0)
        self.assertAlmostEqual(first['y'], 0.0)

    def test_default_attach_is_first_vertex(self):
        loops = build_loops_with_leads(
            [self.SQUARE], feed_rate=100.0, lead_length=0.25, lead_points=[])
        first = loops[0]['segments'][0]
        self.assertEqual((first['x'], first['y']), self.SQUARE[0])

    def test_clicked_point_moves_attach(self):
        loops = build_loops_with_leads(
            [self.SQUARE], feed_rate=100.0, lead_length=0.25,
            lead_points=[(1.95, 2.05)])
        first = loops[0]['segments'][0]
        self.assertEqual((first['x'], first['y']), (2.0, 2.0))

    def test_point_assigned_to_nearest_loop(self):
        perimeter = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
        hole = [(3.0, 3.0), (5.0, 3.0), (5.0, 5.0), (3.0, 5.0)]
        loops = build_loops_with_leads(
            [perimeter, hole], feed_rate=100.0, lead_length=0.25,
            lead_points=[(3.0, 3.0)])
        self.assertEqual((loops[1]['segments'][0]['x'],
                          loops[1]['segments'][0]['y']), (3.0, 3.0))
