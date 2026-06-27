from dataclasses import dataclass, field


@dataclass
class PlasmaConfig:
    pierce_height: float = 0.15
    cut_height: float = 0.063
    pierce_delay: float = 0.5
    first_pierce_time: float = 0.0
    plunge_rate: float = 100.0
    end_delay: float = 0.0
    retract_height: float = 1.0
    ihs_springback: float = 0.020
    units: str = 'inch'


def format_num(value: float) -> str:
    """Format to max 4 decimal places, always with at least one decimal digit."""
    s = f"{value:.4f}".rstrip('0')
    if s.endswith('.'):
        s += '0'
    return s


def sanitize_comment(text: str) -> str:
    """Strip parentheses from comment text to prevent nested comments."""
    return text.replace('(', '').replace(')', '')


class PlasmaPostProcessor:

    # Hardcoded IHS probe constants (inches) — do not expose as user knobs
    _IHS_SEEK_DOWN_Z = -5.0
    _IHS_SEEK_DOWN_F = 100.0
    _IHS_SEEK_UP_Z = 0.5
    _IHS_SEEK_UP_F = 20.0
    _IHS_BACKLASH = 0.02

    _MIN_RAPID_DIST = 0.001   # inches — skip rapids shorter than this
    _MIN_ARC_RADIUS = 0.05    # inches — arcs smaller than this become linear moves

    def _emit_rapid(self, x: float, y: float, cur_x: float, cur_y: float) -> list:
        dist = ((x - cur_x) ** 2 + (y - cur_y) ** 2) ** 0.5
        if dist < self._MIN_RAPID_DIST:
            return []
        parts = ['G0']
        if x != cur_x:
            parts.append(f'X{format_num(x)}')
        if y != cur_y:
            parts.append(f'Y{format_num(y)}')
        return [' '.join(parts)]

    def _emit_linear(self, x: float, y: float, feed: float,
                     prev_x: float, prev_y: float, prev_feed) -> list:
        if x == prev_x and y == prev_y:
            return []
        parts = ['G1']
        if x != prev_x:
            parts.append(f'X{format_num(x)}')
        if y != prev_y:
            parts.append(f'Y{format_num(y)}')
        if feed != prev_feed:
            parts.append(f'F{format_num(feed)}')
        return [' '.join(parts)]

    def _emit_arc(self, x: float, y: float, center_x: float, center_y: float,
                  clockwise: bool, feed: float,
                  cur_x: float, cur_y: float, prev_feed) -> list:
        radius = ((center_x - cur_x) ** 2 + (center_y - cur_y) ** 2) ** 0.5
        if radius < self._MIN_ARC_RADIUS:
            return self._emit_linear(x, y, feed, cur_x, cur_y, prev_feed)
        g = 'G2' if clockwise else 'G3'
        i = format_num(center_x - cur_x)
        j = format_num(center_y - cur_y)
        parts = [g, f'X{format_num(x)}', f'Y{format_num(y)}',
                 f'I{i}', f'J{j}']
        if feed != prev_feed:
            parts.append(f'F{format_num(feed)}')
        return [' '.join(parts)]

    def _split_full_circle(self, center_x: float, center_y: float, radius: float,
                           clockwise: bool, feed: float,
                           start_x: float, start_y: float) -> list:
        # Antipodal point: reflect start through center
        mid_x = 2 * center_x - start_x
        mid_y = 2 * center_y - start_y
        g = 'G2' if clockwise else 'G3'
        i1 = format_num(center_x - start_x)
        j1 = format_num(center_y - start_y)
        i2 = format_num(center_x - mid_x)
        j2 = format_num(center_y - mid_y)
        arc1 = f'{g} X{format_num(mid_x)} Y{format_num(mid_y)} I{i1} J{j1} F{format_num(feed)}'
        arc2 = f'{g} X{format_num(start_x)} Y{format_num(start_y)} I{i2} J{j2}'
        return [arc1, arc2]  # ponytail: radius arg kept for API symmetry, not used (center+start determine it)

    def _emit_preamble(self, config: PlasmaConfig) -> list:
        units_word = 'G21' if config.units == 'mm' else 'G20'
        return ['(v1.6-sc)', 'G90 G94', 'G17', units_word, 'H0']

    def _emit_postamble(self, program_speed: float) -> list:
        return ['M5 M30', f'(PS{int(program_speed)})']

    def _ihs_active(self, config: PlasmaConfig) -> bool:
        return config.pierce_height != 0 and config.cut_height != 0

    def _scale(self, config: PlasmaConfig) -> float:
        return 25.4 if config.units == 'mm' else 1.0

    def _emit_pen_up(self, config: PlasmaConfig) -> list:
        s = self._scale(config)
        lines = ['H0', 'M5']

        if config.end_delay > 0:
            lines.append(f'G4 P{format_num(config.end_delay)}')

        if self._ihs_active(config):
            lines.append(f'G0 Z{format_num(config.retract_height * s)}')

        return lines

    def _emit_pen_down(self, config: PlasmaConfig, is_first_pierce: bool) -> list:
        s = self._scale(config)
        lines = ['']  # blank line separator before each loop

        if self._ihs_active(config):
            lines += [
                'G92 Z0.0',
                f'G38.2 Z{format_num(self._IHS_SEEK_DOWN_Z * s)} F{format_num(self._IHS_SEEK_DOWN_F * s)}',
                f'G38.4 Z{format_num(self._IHS_SEEK_UP_Z * s)} F{format_num(self._IHS_SEEK_UP_F * s)}',
                'G92 Z0.0',
                f'G0 Z{format_num((config.ihs_springback + self._IHS_BACKLASH) * s)} (IHS Backlash)',
                'G92 Z0.0',
                f'G0 Z{format_num(config.pierce_height * s)} (Pierce Height)',
            ]

        lines.append('M3')

        total_dwell = config.pierce_delay + (config.first_pierce_time if is_first_pierce else 0.0)
        if total_dwell > 0:
            lines.append(f'G4 P{format_num(total_dwell)}')

        if self._ihs_active(config):
            lines += [
                f'G1 Z{format_num(config.cut_height * s)} F{format_num(config.plunge_rate * s)} (Cut Height)',
                'H1',
            ]

        return lines

    def generate_gcode(self, loops: list, config: PlasmaConfig) -> str:
        lines = []
        program_speed = 0.0
        lines.extend(self._emit_preamble(config))

        # The machine begins at work zero. Track its physical position across
        # loops so we rapid (G0) to each loop's start before piercing.
        mach_x, mach_y = 0.0, 0.0

        for loop_idx, loop in enumerate(loops):
            is_first = (loop_idx == 0)

            # Rapid to this loop's start BEFORE piercing. Without this the torch
            # pierces wherever it happens to be (the origin for the first loop)
            # and the first cut move drags a diagonal into the part instead of
            # tracing the contour edge.
            lines.extend(self._emit_rapid(
                loop['start_x'], loop['start_y'], mach_x, mach_y))
            mach_x, mach_y = loop['start_x'], loop['start_y']

            lines.extend(self._emit_pen_down(config, is_first_pierce=is_first))

            cur_x = loop['start_x']
            cur_y = loop['start_y']
            prev_feed = None
            # FireControl cancels modal number state after each pen-up
            # (CancelModalNumbers), so the first cut move of every loop must
            # re-emit both X and Y. modal_x/modal_y are None until the first
            # move is emitted, forcing absolute re-establishment; cur_x/cur_y
            # still track true position for arc I/J offsets.
            modal_x = None
            modal_y = None

            for seg in loop['segments']:
                feed = seg['feed']
                if feed > program_speed:
                    program_speed = feed

                if seg['type'] == 'line':
                    move_lines = self._emit_linear(
                        seg['x'], seg['y'], feed, modal_x, modal_y, prev_feed)
                    lines.extend(move_lines)
                    if move_lines:
                        cur_x, cur_y = seg['x'], seg['y']
                        modal_x, modal_y = seg['x'], seg['y']
                        prev_feed = feed

                elif seg['type'] == 'arc':
                    if seg.get('is_full_circle'):
                        import math
                        radius = math.hypot(
                            seg['center_x'] - cur_x, seg['center_y'] - cur_y)
                        move_lines = self._split_full_circle(
                            seg['center_x'], seg['center_y'], radius,
                            seg['clockwise'], feed, cur_x, cur_y)
                    else:
                        move_lines = self._emit_arc(
                            seg['x'], seg['y'],
                            seg['center_x'], seg['center_y'],
                            seg['clockwise'], feed, cur_x, cur_y, prev_feed)
                    lines.extend(move_lines)
                    if move_lines:
                        cur_x = seg.get('x', cur_x)
                        cur_y = seg.get('y', cur_y)
                        modal_x, modal_y = cur_x, cur_y
                        prev_feed = feed

            lines.extend(self._emit_pen_up(config))

            # The torch ends the loop where cutting finished; remember it so the
            # next loop's leading rapid starts from the right place.
            mach_x, mach_y = cur_x, cur_y

        lines.extend(self._emit_postamble(program_speed))
        return '\n'.join(lines)


import math as _math


def _point_in_polygon(x: float, y: float, polygon: list) -> bool:
    """Ray-cast point-in-polygon test. polygon is a list of (x, y) vertices."""
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and \
                (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _polygon_centroid(polygon: list) -> tuple:
    """Average of vertices — sufficient for nesting classification."""
    n = len(polygon)
    sx = sum(p[0] for p in polygon)
    sy = sum(p[1] for p in polygon)
    return (sx / n, sy / n)


def _polygon_area(polygon: list) -> float:
    """Unsigned polygon area via the shoelace formula."""
    n = len(polygon)
    s = 0.0
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _classify_loops(polylines: list) -> list:
    """Return a bool per loop: True if it is a HOLE, False if a perimeter.

    A loop is a hole only when its centroid lies inside another, LARGER loop.
    The area check is essential: for concentric shapes each loop's centroid can
    fall inside the other, so centroid containment alone would flag both as
    holes. Requiring the container to be larger keeps the outer loop a
    perimeter."""
    areas = [_polygon_area(poly) for poly in polylines]
    flags = []
    for i, poly in enumerate(polylines):
        cx, cy = _polygon_centroid(poly)
        is_hole = False
        for j, other in enumerate(polylines):
            if i != j and areas[j] > areas[i] and \
                    _point_in_polygon(cx, cy, other):
                is_hole = True
                break
        flags.append(is_hole)
    return flags


_LEAD_HALF_ANGLE_DEG = 30.0   # each V leg's tilt off the scrap-side normal
_SCRAP_PROBE_EPS = 0.01       # inches — offset used for inside/outside tests


def _rotate_vec(vx: float, vy: float, deg: float) -> tuple:
    r = _math.radians(deg)
    c, s = _math.cos(r), _math.sin(r)
    return (vx * c - vy * s, vx * s + vy * c)


def _compute_v_lead(loop_points: list, attach_idx: int, scrap_outside: bool,
                    lead_length: float) -> dict:
    """Compute the V lead for one loop.

    loop_points   : closed loop vertices (no duplicate closing point)
    attach_idx    : index of the attach vertex C in loop_points
    scrap_outside : True for perimeter loops, False for holes
    lead_length   : leg length (inches)

    Returns {'C', 'P_in', 'P_out', 'contour'} where 'contour' is loop_points
    rotated to start at C with C repeated at the end (closed).
    """
    n = len(loop_points)
    cx, cy = loop_points[attach_idx]
    prev = loop_points[(attach_idx - 1) % n]
    nxt = loop_points[(attach_idx + 1) % n]

    # Tangent through C = averaged neighbor direction
    tx, ty = (nxt[0] - prev[0], nxt[1] - prev[1])
    tlen = _math.hypot(tx, ty) or 1.0
    tx, ty = tx / tlen, ty / tlen

    # Normal candidate (perpendicular to tangent)
    nx, ny = (-ty, tx)

    # Flip the normal so it points INTO scrap.
    probe_inside = _point_in_polygon(
        cx + nx * _SCRAP_PROBE_EPS, cy + ny * _SCRAP_PROBE_EPS, loop_points)
    want_inside = not scrap_outside  # holes: scrap is the polygon interior
    if probe_inside != want_inside:
        nx, ny = -nx, -ny

    in_dx, in_dy = _rotate_vec(nx, ny, _LEAD_HALF_ANGLE_DEG)
    out_dx, out_dy = _rotate_vec(nx, ny, -_LEAD_HALF_ANGLE_DEG)

    p_in = (cx + in_dx * lead_length, cy + in_dy * lead_length)
    p_out = (cx + out_dx * lead_length, cy + out_dy * lead_length)

    rotated = loop_points[attach_idx:] + loop_points[:attach_idx]
    contour = rotated + [(cx, cy)]

    return {'C': (cx, cy), 'P_in': p_in, 'P_out': p_out, 'contour': contour}


def _tessellate_arc(cx: float, cy: float, r: float,
                    start_deg: float, end_deg: float,
                    seg_deg: float = 11.25) -> list:
    """Tessellate a DXF ARC into an ordered point list (CCW from start to end).

    Matches the CIRCLE density (~11.25 deg/segment = 32 points per full turn).
    Both endpoints are included so the result chains onto adjoining segments.
    """
    start = _math.radians(start_deg)
    end = _math.radians(end_deg)
    if end <= start:
        end += 2 * _math.pi
    sweep = end - start
    n = max(2, int(_math.ceil(_math.degrees(sweep) / seg_deg)))
    return [
        (cx + r * _math.cos(start + sweep * i / n),
         cy + r * _math.sin(start + sweep * i / n))
        for i in range(n + 1)
    ]


def _chain_edges(edges: list, tol: float = 0.001) -> list:
    """Chain point-list edges (lines and tessellated arcs) into closed loops.

    Each edge is an ordered list of (x, y) points. Edges are joined end-to-end,
    reversing an edge when only its far endpoint matches. Open chains are
    discarded. The duplicate closing point is dropped from each returned loop.
    """
    def near(a, b):
        return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol

    remaining = [list(e) for e in edges]
    loops = []

    while remaining:
        chain = remaining.pop(0)
        changed = True

        while changed:
            changed = False
            for i, edge in enumerate(remaining):
                es, ee = edge[0], edge[-1]
                if near(chain[-1], es):
                    chain.extend(edge[1:]); remaining.pop(i); changed = True; break
                elif near(chain[-1], ee):
                    chain.extend(reversed(edge[:-1])); remaining.pop(i); changed = True; break
                elif near(chain[0], ee):
                    chain[:0] = edge[:-1]; remaining.pop(i); changed = True; break
                elif near(chain[0], es):
                    chain[:0] = list(reversed(edge[1:])); remaining.pop(i); changed = True; break

        # Accept only closed chains with at least 3 unique points
        if len(chain) >= 4 and near(chain[0], chain[-1]):
            loops.append(chain[:-1])  # drop duplicate closing point

    return loops


def dxf_to_polylines(filepath: str) -> list:
    """Extract closed polylines from a DXF file for plasma cutting.

    Returns a list of closed polylines. Each polyline is a list of (x, y) tuples.
    LINE and ARC entities are chained together (arcs tessellated to points) into
    closed loops; chains that don't close are skipped. Open LWPOLYLINEs and
    SPLINEs are skipped. CIRCLEs are approximated as 32-segment polygons.
    """
    import ezdxf as _ezdxf
    doc = _ezdxf.readfile(filepath)
    msp = doc.modelspace()

    polylines = []
    edges = []  # each edge is an ordered (x, y) point list (line or arc)

    for entity in msp:
        etype = entity.dxftype()

        if etype in ('LWPOLYLINE', 'POLYLINE'):
            pts = [(p[0], p[1]) for p in entity.get_points()]
            if len(pts) < 3:
                continue
            closed = getattr(entity, 'is_closed', False) or (
                abs(pts[0][0] - pts[-1][0]) < 0.001 and
                abs(pts[0][1] - pts[-1][1]) < 0.001
            )
            if not closed:
                continue
            # Remove the duplicate closing point if present
            if (abs(pts[0][0] - pts[-1][0]) < 0.001 and
                    abs(pts[0][1] - pts[-1][1]) < 0.001):
                pts = pts[:-1]
            polylines.append(pts)

        elif etype == 'LINE':
            start = (entity.dxf.start.x, entity.dxf.start.y)
            end = (entity.dxf.end.x, entity.dxf.end.y)
            edges.append([start, end])

        elif etype == 'ARC':
            edges.append(_tessellate_arc(
                entity.dxf.center.x, entity.dxf.center.y, entity.dxf.radius,
                entity.dxf.start_angle, entity.dxf.end_angle))

        elif etype == 'CIRCLE':
            cx = entity.dxf.center.x
            cy = entity.dxf.center.y
            r = entity.dxf.radius
            n = 32
            pts = [
                (cx + r * _math.cos(2 * _math.pi * i / n),
                 cy + r * _math.sin(2 * _math.pi * i / n))
                for i in range(n)
            ]
            polylines.append(pts)

        # SPLINE and all others: silently skipped

    if edges:
        polylines.extend(_chain_edges(edges))

    return polylines


def normalize_to_origin(polylines: list) -> list:
    """Translate all polylines so the bottom-left of their combined bounding box
    sits at (0, 0).

    DXF files exported from CAD keep their model-space coordinates, which can be
    anywhere. The mill pipeline normalizes the part to a chosen origin corner
    (see FRCPostProcessor.transform_coordinates); plasma must do the same so the
    G-code lives in the positive quadrant and the toolpath aligns with the stock
    in the viewer. Relative positions between loops are preserved (single shared
    offset).
    """
    if not polylines:
        return polylines

    min_x = min(x for poly in polylines for x, y in poly)
    min_y = min(y for poly in polylines for x, y in poly)
    if min_x == 0 and min_y == 0:
        return polylines

    return [[(x - min_x, y - min_y) for x, y in poly] for poly in polylines]


def polylines_to_loops(polylines: list, feed_rate: float) -> list:
    """Convert DXF polylines (list of (x,y) point lists) to cut loop dicts."""
    loops = []
    for points in polylines:
        if len(points) < 2:
            continue
        start_x, start_y = points[0]
        segments = []
        for pt in points[1:]:
            segments.append({'type': 'line', 'x': pt[0], 'y': pt[1], 'feed': feed_rate})
        # Close the loop if not already closed
        last = segments[-1]
        if last['x'] != start_x or last['y'] != start_y:
            segments.append({'type': 'line', 'x': start_x, 'y': start_y, 'feed': feed_rate})
        loops.append({'start_x': start_x, 'start_y': start_y, 'segments': segments})
    return loops


def _nearest_vertex_index(loop_points: list, x: float, y: float) -> tuple:
    """Return (index, distance) of the vertex nearest (x, y)."""
    best_i, best_d = 0, float('inf')
    for i, (px, py) in enumerate(loop_points):
        d = _math.hypot(px - x, py - y)
        if d < best_d:
            best_i, best_d = i, d
    return best_i, best_d


def build_loops_with_leads(polylines: list, feed_rate: float,
                           lead_length: float, lead_points: list) -> list:
    """Build cut-loop dicts with a straight V lead-in/lead-out baked in.

    polylines   : closed (x, y) point lists (already normalized to origin)
    feed_rate   : IPM for all cut segments
    lead_length : leg length (inches); 0 disables leads entirely
    lead_points : list of (x, y) attach hints in the same coord space; may be []

    Returns the same shape as polylines_to_loops(). With leads, start_x/start_y
    is the pierce point P_in, the first segment is the lead-in line to C, the
    contour follows, and the last segment is the lead-out line to P_out.
    """
    if lead_length <= 0:
        return polylines_to_loops(polylines, feed_rate)

    is_hole = _classify_loops(polylines)

    # Assign each clicked point to its nearest loop (nearest vertex wins).
    assigned = {}  # loop_idx -> (dist, attach_idx)
    for (lx, ly) in lead_points:
        best_loop, best_idx, best_d = None, None, float('inf')
        for li, poly in enumerate(polylines):
            idx, d = _nearest_vertex_index(poly, lx, ly)
            if d < best_d:
                best_loop, best_idx, best_d = li, idx, d
        if best_loop is not None:
            prev = assigned.get(best_loop)
            if prev is None or best_d < prev[0]:
                assigned[best_loop] = (best_d, best_idx)

    loops = []
    for li, poly in enumerate(polylines):
        if len(poly) < 2:
            continue
        attach_idx = assigned[li][1] if li in assigned else 0
        v = _compute_v_lead(poly, attach_idx, scrap_outside=not is_hole[li],
                            lead_length=lead_length)

        cx, cy = v['C']
        segments = [{'type': 'line', 'x': cx, 'y': cy, 'feed': feed_rate}]
        for (px, py) in v['contour'][1:]:
            segments.append({'type': 'line', 'x': px, 'y': py, 'feed': feed_rate})
        ox, oy = v['P_out']
        segments.append({'type': 'line', 'x': ox, 'y': oy, 'feed': feed_rate})

        loops.append({
            'start_x': v['P_in'][0],
            'start_y': v['P_in'][1],
            'segments': segments,
        })
    return loops
