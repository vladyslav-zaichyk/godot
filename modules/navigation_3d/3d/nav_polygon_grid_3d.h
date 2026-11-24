/**************************************************************************/
/*  nav_polygon_grid_3d.h                                                 */
/**************************************************************************/
/*                         This file is part of:                          */
/*                             GODOT ENGINE                               */
/*                        https://godotengine.org                         */
/**************************************************************************/
/* Copyright (c) 2014-present Godot Engine contributors (see AUTHORS.md). */
/* Copyright (c) 2007-2014 Juan Linietsky, Ariel Manzur.                  */
/*                                                                        */
/* Permission is hereby granted, free of charge, to any person obtaining  */
/* a copy of this software and associated documentation files (the        */
/* "Software"), to deal in the Software without restriction, including    */
/* without limitation the rights to use, copy, modify, merge, publish,    */
/* distribute, sublicense, and/or sell copies of the Software, and to     */
/* permit persons to whom the Software is furnished to do so, subject to  */
/* the following conditions:                                              */
/*                                                                        */
/* The above copyright notice and this permission notice shall be         */
/* included in all copies or substantial portions of the Software.        */
/*                                                                        */
/* THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,        */
/* EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF     */
/* MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. */
/* IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY   */
/* CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,   */
/* TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE      */
/* SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.                 */
/**************************************************************************/

#pragma once

// Arena modification: a grid over a region's polygons, so that a closest point query tests the
// polygons near the point instead of all of them.

#include "../nav_utils_3d.h"

#include "core/math/aabb.h"
#include "core/math/vector2.h"
#include "core/templates/local_vector.h"

// Cells over the region's polygons on the XZ plane. A polygon is listed in every cell its bounds
// overlap. A search goes out from the point's cell ring by ring and stops once the best distance
// found is shorter than the distance to any ring not yet searched. It is built with the region's
// iteration and only read after that, so queries on other threads may share it.
struct NavPolygonGrid3D {
	// About this many polygons a cell, if they were spread evenly over the bounds.
	static constexpr real_t POLYGONS_PER_CELL = 2.0;
	static constexpr real_t MIN_CELL_SIZE = 0.25;
	static constexpr int64_t MAX_CELLS = 1 << 20;
	// A point this many cells away from the grid is searched polygon by polygon.
	static constexpr real_t MAX_CELL_DISTANCE = 1 << 20;
	// Taken off the distance to the rings not yet searched, so that float rounding never stops a
	// search before a polygon that is as close.
	static constexpr real_t STOP_MARGIN = 0.001;
	// Bounds are only skipped when they are farther than the best by more than float rounding, so
	// that a polygon exactly as close as the best is still visited.
	static constexpr real_t SKIP_SLACK = 1e-5;
	static constexpr real_t SKIP_SLACK_MIN = 1e-9;

	Vector2 origin;
	real_t cell_size = 1.0;
	real_t inv_cell_size = 1.0;
	int64_t width = 0;
	int64_t depth = 0;
	// Cell (x, z) lists cell_polygons[cell_offsets[c]] up to cell_polygons[cell_offsets[c + 1]],
	// with c = z * width + x.
	LocalVector<uint32_t> cell_offsets;
	LocalVector<uint32_t> cell_polygons;
	// By polygon index. A polygon with fewer than 3 vertices is in no cell.
	LocalVector<AABB> polygon_bounds;
	LocalVector<uint32_t> listed_polygons;

	void clear() {
		width = 0;
		depth = 0;
		cell_offsets.clear();
		cell_polygons.clear();
		polygon_bounds.clear();
		listed_polygons.clear();
	}

	bool is_empty() const { return width == 0 || depth == 0; }

	void build(const LocalVector<Nav3D::Polygon> &p_polygons);

	static _FORCE_INLINE_ real_t aabb_distance_squared(const AABB &p_aabb, const Vector3 &p_point) {
		const Vector3 end = p_aabb.position + p_aabb.size;
		const real_t dx = MAX(MAX(p_aabb.position.x - p_point.x, (real_t)0.0), p_point.x - end.x);
		const real_t dy = MAX(MAX(p_aabb.position.y - p_point.y, (real_t)0.0), p_point.y - end.y);
		const real_t dz = MAX(MAX(p_aabb.position.z - p_point.z, (real_t)0.0), p_point.z - end.z);
		return dx * dx + dy * dy + dz * dz;
	}

	// Whether something at p_distance_squared at the least cannot be as close as the best.
	static _FORCE_INLINE_ bool is_beyond(real_t p_distance_squared, real_t p_best_distance_squared) {
		return p_distance_squared > p_best_distance_squared + p_best_distance_squared * SKIP_SLACK + SKIP_SLACK_MIN;
	}

	// Calls p_visit(polygon_index) for the polygons that can be closer to p_point than
	// r_best_distance_squared, which p_visit lowers as it finds closer ones. A polygon may be
	// visited more than once. Polygons as close as the best are visited too, so p_visit can break
	// ties the way a scan in index order would.
	template <typename F>
	void search(const Vector3 &p_point, const real_t &r_best_distance_squared, F &&p_visit) const {
		if (is_empty()) {
			return;
		}

		const real_t fx = (p_point.x - origin.x) * inv_cell_size;
		const real_t fz = (p_point.z - origin.y) * inv_cell_size;
		if (!(Math::abs(fx) < MAX_CELL_DISTANCE && Math::abs(fz) < MAX_CELL_DISTANCE)) {
			// Far away, or not a number: every polygon, with only their bounds to skip them.
			for (uint32_t polygon_index : listed_polygons) {
				if (!is_beyond(aabb_distance_squared(polygon_bounds[polygon_index], p_point), r_best_distance_squared)) {
					p_visit(polygon_index);
				}
			}
			return;
		}

		const int64_t cx = (int64_t)Math::floor(fx);
		const int64_t cz = (int64_t)Math::floor(fz);
		const int64_t last_x = width - 1;
		const int64_t last_z = depth - 1;
		// The rings before this one hold no cells of the grid.
		const int64_t first_ring = MAX(cx < 0 ? -cx : (cx > last_x ? cx - last_x : 0), cz < 0 ? -cz : (cz > last_z ? cz - last_z : 0));
		// This one holds the grid's last cells.
		const int64_t last_ring = MAX(MAX(cx, last_x - cx), MAX(cz, last_z - cz));

		for (int64_t ring = first_ring; ring <= last_ring; ring++) {
			if (ring > 0) {
				// Every polygon not yet visited lies outside the square of the rings before this
				// one, at least as far as its sides.
				const real_t min_x = origin.x + (cx - ring + 1) * cell_size;
				const real_t max_x = origin.x + (cx + ring) * cell_size;
				const real_t min_z = origin.y + (cz - ring + 1) * cell_size;
				const real_t max_z = origin.y + (cz + ring) * cell_size;
				const real_t outside = MIN(MIN(p_point.x - min_x, max_x - p_point.x), MIN(p_point.z - min_z, max_z - p_point.z)) - STOP_MARGIN;
				if (outside > 0.0 && r_best_distance_squared < outside * outside) {
					return;
				}
			}

			if (ring == 0) {
				_visit_cell(cx, cz, p_point, r_best_distance_squared, p_visit);
				continue;
			}

			const int64_t x0 = cx - ring;
			const int64_t x1 = cx + ring;
			const int64_t z0 = cz - ring;
			const int64_t z1 = cz + ring;
			// The ring's top and bottom rows, then its left and right columns between them.
			_visit_row(z0, MAX(x0, (int64_t)0), MIN(x1, last_x), p_point, r_best_distance_squared, p_visit);
			_visit_row(z1, MAX(x0, (int64_t)0), MIN(x1, last_x), p_point, r_best_distance_squared, p_visit);
			_visit_column(x0, MAX(z0 + 1, (int64_t)0), MIN(z1 - 1, last_z), p_point, r_best_distance_squared, p_visit);
			_visit_column(x1, MAX(z0 + 1, (int64_t)0), MIN(z1 - 1, last_z), p_point, r_best_distance_squared, p_visit);
		}
	}

private:
	template <typename F>
	_FORCE_INLINE_ void _visit_row(int64_t p_z, int64_t p_from_x, int64_t p_to_x, const Vector3 &p_point, const real_t &r_best_distance_squared, F &p_visit) const {
		if (p_z < 0 || p_z >= depth) {
			return;
		}
		for (int64_t x = p_from_x; x <= p_to_x; x++) {
			_visit_cell(x, p_z, p_point, r_best_distance_squared, p_visit);
		}
	}

	template <typename F>
	_FORCE_INLINE_ void _visit_column(int64_t p_x, int64_t p_from_z, int64_t p_to_z, const Vector3 &p_point, const real_t &r_best_distance_squared, F &p_visit) const {
		if (p_x < 0 || p_x >= width) {
			return;
		}
		for (int64_t z = p_from_z; z <= p_to_z; z++) {
			_visit_cell(p_x, z, p_point, r_best_distance_squared, p_visit);
		}
	}

	template <typename F>
	_FORCE_INLINE_ void _visit_cell(int64_t p_x, int64_t p_z, const Vector3 &p_point, const real_t &r_best_distance_squared, F &p_visit) const {
		if (p_x < 0 || p_x >= width || p_z < 0 || p_z >= depth) {
			return;
		}
		// The cell's own bounds first. A polygon that can be closer than the best is also listed in
		// the cell that holds its closest point, which is no farther than that point.
		const real_t min_x = origin.x + p_x * cell_size;
		const real_t min_z = origin.y + p_z * cell_size;
		const real_t dx = MAX(MAX(min_x - p_point.x, (real_t)0.0), p_point.x - (min_x + cell_size));
		const real_t dz = MAX(MAX(min_z - p_point.z, (real_t)0.0), p_point.z - (min_z + cell_size));
		if (is_beyond(dx * dx + dz * dz, r_best_distance_squared)) {
			return;
		}

		const int64_t cell = p_z * width + p_x;
		const uint32_t end = cell_offsets[cell + 1];
		for (uint32_t i = cell_offsets[cell]; i < end; i++) {
			const uint32_t polygon_index = cell_polygons[i];
			if (!is_beyond(aabb_distance_squared(polygon_bounds[polygon_index], p_point), r_best_distance_squared)) {
				p_visit(polygon_index);
			}
		}
	}
};
