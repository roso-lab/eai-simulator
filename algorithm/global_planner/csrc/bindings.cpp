#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>
#include "planner_core.h"

namespace py = pybind11;

PYBIND11_MODULE(_planner_cpp, m) {
    m.doc() = "C++ accelerated grid planner (A*, nearest_free, collision check, ST-A*)";

    py::class_<GridPlanner>(m, "GridPlanner")
        .def(py::init<>())
        .def("set_grid", [](GridPlanner& self,
                            py::array_t<uint8_t, py::array::c_style | py::array::forcecast> arr,
                            double res, double ox, double oy) {
            auto buf = arr.request();
            if (buf.ndim != 2)
                throw std::runtime_error("grid must be 2D");
            int h = (int)buf.shape[0], w = (int)buf.shape[1];
            self.set_grid(static_cast<const uint8_t*>(buf.ptr), h, w, res, ox, oy);
        }, py::arg("grid"), py::arg("resolution"), py::arg("origin_x"), py::arg("origin_y"))
        .def("inflate", &GridPlanner::inflate, py::arg("radius"))
        .def("is_free", &GridPlanner::is_free)
        .def("in_bounds", &GridPlanner::in_bounds)
        .def("world_to_grid", [](const GridPlanner& self, double x, double y) {
            auto c = self.world_to_grid(x, y);
            return py::make_tuple(c.i, c.j);
        })
        .def("grid_to_world", [](const GridPlanner& self, int i, int j) {
            auto v = self.grid_to_world(i, j);
            return py::make_tuple(v.x, v.y);
        })
        .def("nearest_free", [](const GridPlanner& self, int i, int j, int max_r) {
            auto c = self.nearest_free(i, j, max_r);
            return py::make_tuple(c.i, c.j);
        }, py::arg("i"), py::arg("j"), py::arg("max_radius") = 20)
        .def("collision_free_segment", &GridPlanner::collision_free_segment,
             py::arg("x0"), py::arg("y0"), py::arg("x1"), py::arg("y1"),
             py::arg("clearance") = 0)
        .def("a_star", [](const GridPlanner& self,
                          py::tuple start, py::tuple goal) {
            IJ s = {start[0].cast<int>(), start[1].cast<int>()};
            IJ g = {goal[0].cast<int>(), goal[1].cast<int>()};
            auto path = self.a_star(s, g);
            py::list out;
            for (auto& c : path) out.append(py::make_tuple(c.i, c.j));
            return out;
        }, py::arg("start"), py::arg("goal"))
        .def("simplify_path", [](const GridPlanner& self,
                                  const std::vector<std::pair<double,double>>& pts,
                                  int clearance) {
            std::vector<Vec2> vpts;
            vpts.reserve(pts.size());
            for (auto& p : pts) vpts.push_back({p.first, p.second});
            auto out = self.simplify_path(vpts, clearance);
            py::list result;
            for (auto& v : out) result.append(py::make_tuple(v.x, v.y));
            return result;
        }, py::arg("points"), py::arg("clearance") = 1)
        .def("downsample_path", [](const GridPlanner& self,
                                    const std::vector<std::pair<double,double>>& pts,
                                    double step, int clearance) {
            std::vector<Vec2> vpts;
            vpts.reserve(pts.size());
            for (auto& p : pts) vpts.push_back({p.first, p.second});
            auto out = self.downsample_path(vpts, step, clearance);
            py::list result;
            for (auto& v : out) result.append(py::make_tuple(v.x, v.y));
            return result;
        }, py::arg("points"), py::arg("step"), py::arg("clearance") = 2)
        .def_readonly("height", &GridPlanner::height_)
        .def_readonly("width", &GridPlanner::width_)
        .def("get_grid", [](const GridPlanner& self) {
            return py::array_t<uint8_t>(
                {self.height_, self.width_},
                {(ssize_t)self.width_, (ssize_t)1},
                self.grid_.data()
            );
        })
        ;

    py::class_<SpaceTimeAStarCpp>(m, "SpaceTimeAStar")
        .def(py::init<>())
        .def("set_planner", [](SpaceTimeAStarCpp& self, GridPlanner* p) {
            self.planner_ = p;
        }, py::keep_alive<1, 2>())
        .def("clear", &SpaceTimeAStarCpp::clear)
        .def("clear_robot", &SpaceTimeAStarCpp::clear_robot)
        .def("reserve_path", [](SpaceTimeAStarCpp& self,
                                const std::vector<std::pair<int,int>>& path,
                                const std::string& name) {
            std::vector<IJ> ij_path;
            ij_path.reserve(path.size());
            for (auto& p : path) ij_path.push_back({p.first, p.second});
            self.reserve_path(ij_path, name);
        })
        .def("plan", [](SpaceTimeAStarCpp& self,
                         py::tuple start, py::tuple goal,
                         const std::string& robot) -> py::object {
            IJ s = {start[0].cast<int>(), start[1].cast<int>()};
            IJ g = {goal[0].cast<int>(), goal[1].cast<int>()};
            auto path = self.plan(s, g, robot);
            if (path.empty()) return py::none();
            py::list out;
            for (auto& c : path) out.append(py::make_tuple(c.i, c.j));
            return out;
        }, py::arg("start"), py::arg("goal"), py::arg("robot_name"))
        .def("max_reserved_t", &SpaceTimeAStarCpp::max_reserved_t)
        .def_readwrite("max_time_horizon", &SpaceTimeAStarCpp::max_t_)
        .def_readwrite("max_expansions", &SpaceTimeAStarCpp::max_expansions_)
        ;
}
