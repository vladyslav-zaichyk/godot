/**************************************************************************/
/*  nav_polygon_grid_3d.cpp                                               */
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

#include "nav_polygon_grid_3d.h"

using namespace Nav3D;

void NavPolygonGrid3D::build(const LocalVector<Polygon> &p_polygons) {
	clear();

	polygon_bounds.resize(p_polygons.size());
	AABB all;
	for (uint32_t i = 0; i < p_polygons.size(); i++) {
		const LocalVector<Vector3> &vertices = p_polygons[i].vertices;
		if (vertices.size() < 3) {
			continue;
		}
		AABB bounds(vertices[0], Vector3());
		for (uint32_t j = 1; j < vertices.size(); j++) {
			bounds.expand_to(vertices[j]);
		}
		polygon_bounds[i] = bounds;
		if (listed_polygons.is_empty()) {
			all = bounds;
		} else {
			all.merge_with(bounds);
		}
		listed_polygons.push_back(i);
	}

	if (listed_polygons.is_empty()) {
		clear();
		return;
	}

	origin = Vector2(all.position.x, all.position.z);
	const double size_x = all.size.x;
	const double size_z = all.size.z;
	double cell = Math::sqrt(MAX(size_x * size_z, 1.0) * POLYGONS_PER_CELL / listed_polygons.size());
	cell = MAX(cell, (double)MIN_CELL_SIZE);
	double cells_x = Math::floor(size_x / cell) + 1.0;
	double cells_z = Math::floor(size_z / cell) + 1.0;
	while (cells_x * cells_z > MAX_CELLS) {
		cell *= Math::sqrt(cells_x * cells_z / MAX_CELLS) * 1.01;
		cells_x = Math::floor(size_x / cell) + 1.0;
		cells_z = Math::floor(size_z / cell) + 1.0;
	}
	cell_size = cell;
	inv_cell_size = 1.0 / cell;
	width = (int64_t)cells_x;
	depth = (int64_t)cells_z;

	const int64_t cell_count = width * depth;
	cell_offsets.resize(cell_count + 1);
	for (uint32_t &offset : cell_offsets) {
		offset = 0;
	}

	// The cells a polygon's bounds overlap. A bound on a cell's edge counts in the cell after it.
	auto cell_range = [this](const AABB &p_bounds, int64_t &r_x0, int64_t &r_x1, int64_t &r_z0, int64_t &r_z1) {
		const Vector3 end = p_bounds.position + p_bounds.size;
		r_x0 = CLAMP((int64_t)Math::floor((p_bounds.position.x - origin.x) * inv_cell_size), (int64_t)0, width - 1);
		r_x1 = CLAMP((int64_t)Math::floor((end.x - origin.x) * inv_cell_size), (int64_t)0, width - 1);
		r_z0 = CLAMP((int64_t)Math::floor((p_bounds.position.z - origin.y) * inv_cell_size), (int64_t)0, depth - 1);
		r_z1 = CLAMP((int64_t)Math::floor((end.z - origin.y) * inv_cell_size), (int64_t)0, depth - 1);
	};

	int64_t x0, x1, z0, z1;
	for (uint32_t polygon_index : listed_polygons) {
		cell_range(polygon_bounds[polygon_index], x0, x1, z0, z1);
		for (int64_t z = z0; z <= z1; z++) {
			for (int64_t x = x0; x <= x1; x++) {
				cell_offsets[z * width + x + 1]++;
			}
		}
	}
	for (int64_t c = 0; c < cell_count; c++) {
		cell_offsets[c + 1] += cell_offsets[c];
	}

	cell_polygons.resize(cell_offsets[cell_count]);
	LocalVector<uint32_t> filled;
	filled.resize(cell_count);
	for (int64_t c = 0; c < cell_count; c++) {
		filled[c] = cell_offsets[c];
	}
	// In index order within each cell, as a scan of all the polygons would meet them.
	for (uint32_t polygon_index : listed_polygons) {
		cell_range(polygon_bounds[polygon_index], x0, x1, z0, z1);
		for (int64_t z = z0; z <= z1; z++) {
			for (int64_t x = x0; x <= x1; x++) {
				cell_polygons[filled[z * width + x]++] = polygon_index;
			}
		}
	}
}
