#pragma once
#include <cstdint>
#include <cmath>
#include <vector>
#include <queue>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <algorithm>
#include <stdexcept>
#include <functional>

struct Vec2 { double x, y; };
struct IJ   { int i, j; };

inline bool operator==(IJ a, IJ b) { return a.i == b.i && a.j == b.j; }

struct IJHash {
    std::size_t operator()(IJ c) const {
        return std::hash<long long>()(((long long)c.i << 32) | (unsigned int)c.j);
    }
};

class GridPlanner {
public:
    int height_ = 0, width_ = 0;
    double resolution_ = 0.05, origin_x_ = 0, origin_y_ = 0;
    std::vector<uint8_t> grid_;

    void set_grid(const uint8_t* data, int h, int w,
                  double res, double ox, double oy) {
        height_ = h; width_ = w;
        resolution_ = res; origin_x_ = ox; origin_y_ = oy;
        grid_.assign(data, data + (size_t)h * w);
    }

    void inflate(int radius) {
        if (radius <= 0) return;
        std::vector<uint8_t> out(grid_);
        for (int oi = 0; oi < height_; ++oi) {
            for (int oj = 0; oj < width_; ++oj) {
                if (grid_[oi * width_ + oj] == 0) continue;
                int i0 = std::max(0, oi - radius);
                int i1 = std::min(height_ - 1, oi + radius);
                int j0 = std::max(0, oj - radius);
                int j1 = std::min(width_ - 1, oj + radius);
                for (int ni = i0; ni <= i1; ++ni)
                    for (int nj = j0; nj <= j1; ++nj)
                        out[ni * width_ + nj] = 1;
            }
        }
        grid_ = std::move(out);
    }

    inline bool in_bounds(int i, int j) const {
        return (unsigned)i < (unsigned)height_ && (unsigned)j < (unsigned)width_;
    }
    inline bool is_free(int i, int j) const {
        return in_bounds(i, j) && grid_[i * width_ + j] == 0;
    }

    IJ world_to_grid(double x, double y) const {
        return {(int)((y - origin_y_) / resolution_),
                (int)((x - origin_x_) / resolution_)};
    }
    Vec2 grid_to_world(int i, int j) const {
        return {origin_x_ + (j + 0.5) * resolution_,
                origin_y_ + (i + 0.5) * resolution_};
    }

    IJ nearest_free(int i, int j, int max_radius = 20) const {
        if (is_free(i, j)) return {i, j};
        int best_i = i, best_j = j;
        int best_d = 999999;
        for (int r = 1; r <= max_radius; ++r) {
            bool found = false;
            for (int di = -r; di <= r; ++di) {
                for (int dj = -r; dj <= r; ++dj) {
                    int ni = i + di, nj = j + dj;
                    if (!is_free(ni, nj)) continue;
                    int d = std::abs(di) + std::abs(dj);
                    if (d < best_d) { best_d = d; best_i = ni; best_j = nj; found = true; }
                }
            }
            if (found) return {best_i, best_j};
        }
        return {i, j};
    }

    bool collision_free_segment(double x0, double y0, double x1, double y1,
                                int clearance = 0) const {
        double dist = std::hypot(x1 - x0, y1 - y0);
        if (dist < 1e-6) {
            auto c = world_to_grid(x0, y0);
            if (!in_bounds(c.i, c.j)) return false;
            return grid_[c.i * width_ + c.j] == 0;
        }
        double step = resolution_ * 0.5;
        int num = std::max(2, (int)std::ceil(dist / step));
        for (int k = 0; k <= num; ++k) {
            double t = (double)k / num;
            double x = x0 + t * (x1 - x0);
            double y = y0 + t * (y1 - y0);
            auto c = world_to_grid(x, y);
            if (!in_bounds(c.i, c.j)) return false;
            if (clearance > 0) {
                for (int di = -clearance; di <= clearance; ++di)
                    for (int dj = -clearance; dj <= clearance; ++dj) {
                        int ni = c.i + di, nj = c.j + dj;
                        if (!in_bounds(ni, nj) || grid_[ni * width_ + nj] != 0)
                            return false;
                    }
            } else {
                if (grid_[c.i * width_ + c.j] != 0) return false;
            }
        }
        return true;
    }

    std::vector<IJ> a_star(IJ start, IJ goal) const {
        if (!is_free(start.i, start.j))
            throw std::runtime_error("Start cell is not free.");
        if (!is_free(goal.i, goal.j))
            throw std::runtime_error("Goal cell is not free.");

        struct Node {
            double f;
            IJ pos;
            bool operator>(const Node& o) const { return f > o.f; }
        };
        std::priority_queue<Node, std::vector<Node>, std::greater<Node>> open;
        open.push({0.0, start});

        std::unordered_map<IJ, IJ, IJHash> came_from;
        std::unordered_map<IJ, double, IJHash> g_score;
        came_from[start] = {-1, -1};
        g_score[start] = 0.0;

        static const int DI[] = {-1,-1,-1, 0,0, 1,1,1};
        static const int DJ[] = {-1, 0, 1,-1,1,-1,0,1};

        while (!open.empty()) {
            auto cur = open.top(); open.pop();
            int ci = cur.pos.i, cj = cur.pos.j;

            if (ci == goal.i && cj == goal.j) {
                std::vector<IJ> path;
                IJ c = goal;
                while (c.i != -1) {
                    path.push_back(c);
                    c = came_from[c];
                }
                std::reverse(path.begin(), path.end());
                return path;
            }

            double cur_g = g_score[cur.pos];
            if (cur.f > cur_g + std::hypot(ci - goal.i, cj - goal.j) + 1e-6)
                continue;

            for (int d = 0; d < 8; ++d) {
                int ni = ci + DI[d], nj = cj + DJ[d];
                if (!is_free(ni, nj)) continue;
                bool diag = (DI[d] != 0 && DJ[d] != 0);
                if (diag) {
                    if (!is_free(ci + DI[d], cj) || !is_free(ci, cj + DJ[d]))
                        continue;
                }
                double step_cost = diag ? 1.4142135623730951 : 1.0;
                double new_g = cur_g + step_cost;
                IJ nb = {ni, nj};
                auto it = g_score.find(nb);
                if (it == g_score.end() || new_g < it->second) {
                    g_score[nb] = new_g;
                    double h = std::hypot(ni - goal.i, nj - goal.j);
                    open.push({new_g + h, nb});
                    came_from[nb] = cur.pos;
                }
            }
        }
        throw std::runtime_error("A* failed to find a path.");
    }

    std::vector<Vec2> simplify_path(const std::vector<Vec2>& pts, int clearance) const {
        if (pts.size() <= 2) return pts;
        std::vector<Vec2> out;
        out.push_back(pts[0]);
        size_t i = 0, n = pts.size();
        while (i < n - 1) {
            size_t j = n - 1;
            while (j > i + 1) {
                if (collision_free_segment(pts[i].x, pts[i].y, pts[j].x, pts[j].y, clearance))
                    break;
                --j;
            }
            out.push_back(pts[j]);
            i = j;
        }
        return out;
    }

    std::vector<Vec2> downsample_path(const std::vector<Vec2>& pts, double step,
                                       int clearance) const {
        if (pts.empty()) return {};
        std::vector<Vec2> out;
        out.push_back(pts[0]);
        size_t idx = 0;
        for (size_t j = 1; j < pts.size(); ++j) {
            auto& prev = out.back();
            double dist = std::hypot(pts[j].x - prev.x, pts[j].y - prev.y);
            if (collision_free_segment(prev.x, prev.y, pts[j].x, pts[j].y, clearance)) {
                if (dist >= step) { out.push_back(pts[j]); idx = j; }
            } else {
                for (size_t k = idx + 1; k <= j; ++k) out.push_back(pts[k]);
                idx = j;
            }
        }
        if (out.back().x != pts.back().x || out.back().y != pts.back().y)
            out.push_back(pts.back());
        return out;
    }
};

struct STNode {
    int i, j, t;
    bool operator==(const STNode& o) const { return i == o.i && j == o.j && t == o.t; }
};
struct STNodeHash {
    std::size_t operator()(const STNode& n) const {
        std::size_t h = std::hash<int>()(n.i);
        h ^= std::hash<int>()(n.j) + 0x9e3779b9 + (h << 6) + (h >> 2);
        h ^= std::hash<int>()(n.t) + 0x9e3779b9 + (h << 6) + (h >> 2);
        return h;
    }
};

class SpaceTimeAStarCpp {
public:
    const GridPlanner* planner_;
    int max_t_ = 256, max_expansions_ = 200000;

    std::unordered_map<STNode, std::string, STNodeHash> vertex_;
    std::unordered_map<long long, std::string> edge_;

    void clear() { vertex_.clear(); edge_.clear(); }

    void clear_robot(const std::string& name) {
        for (auto it = vertex_.begin(); it != vertex_.end();)
            it = (it->second == name) ? vertex_.erase(it) : std::next(it);
        for (auto it = edge_.begin(); it != edge_.end();)
            it = (it->second == name) ? edge_.erase(it) : std::next(it);
    }

    void reserve_vertex(int i, int j, int t, const std::string& name) {
        vertex_[{i, j, t}] = name;
    }

    static long long edge_key(int i1, int j1, int t1, int i2, int j2, int t2) {
        long long k = 0;
        k = (long long)(i1 + 10000) * 100000LL + (j1 + 10000);
        k = k * 1000 + t1;
        k = k * 100000000LL + (long long)(i2 + 10000) * 100000LL + (j2 + 10000);
        k = k * 1000 + t2;
        return k;
    }

    void reserve_edge(int i1, int j1, int i2, int j2, int t, const std::string& name) {
        edge_[edge_key(i1, j1, t, i2, j2, t + 1)] = name;
    }

    void reserve_path(const std::vector<IJ>& path, const std::string& name) {
        for (int t = 0; t < (int)path.size(); ++t) {
            reserve_vertex(path[t].i, path[t].j, t, name);
            if (t > 0)
                reserve_edge(path[t-1].i, path[t-1].j, path[t].i, path[t].j, t-1, name);
        }
        if (!path.empty()) {
            auto& last = path.back();
            int last_t = (int)path.size() - 1;
            for (int et = last_t + 1; et < last_t + 30; ++et)
                reserve_vertex(last.i, last.j, et, name);
        }
    }

    int max_reserved_t() const {
        int mx = 0;
        for (auto& kv : vertex_) mx = std::max(mx, kv.first.t);
        return mx;
    }

    bool is_vertex_free(int i, int j, int t, const std::string& name) const {
        auto it = vertex_.find({i, j, t});
        return it == vertex_.end() || it->second == name;
    }
    bool is_edge_free(int i1, int j1, int i2, int j2, int t, const std::string& name) const {
        auto it = edge_.find(edge_key(i2, j2, t, i1, j1, t + 1));
        return it == edge_.end() || it->second == name;
    }

    std::vector<IJ> plan(IJ start, IJ goal, const std::string& robot) {
        auto* p = planner_;
        if (!p->is_free(start.i, start.j) || !p->is_free(goal.i, goal.j))
            return {};

        double base_h = std::hypot(start.i - goal.i, start.j - goal.j);
        int dynamic_cap = std::min(max_t_,
            std::max({96, (int)(base_h * 4.0) + 40, max_reserved_t() + 40}));

        struct Node {
            double f, g;
            int t, i, j;
            bool operator>(const Node& o) const { return f > o.f; }
        };
        std::priority_queue<Node, std::vector<Node>, std::greater<Node>> open;
        double h0 = std::hypot(start.i - goal.i, start.j - goal.j);
        open.push({h0, 0.0, 0, start.i, start.j});

        std::unordered_map<STNode, STNode, STNodeHash> came_from;
        std::unordered_map<STNode, double, STNodeHash> g_score;
        STNode s0 = {start.i, start.j, 0};
        came_from[s0] = {-1, -1, -1};
        g_score[s0] = 0.0;

        static const int DI[] = {0,-1,-1,-1, 0,0, 1,1,1};
        static const int DJ[] = {0,-1, 0, 1,-1,1,-1,0,1};

        int expansions = 0;
        while (!open.empty()) {
            auto cur = open.top(); open.pop();
            ++expansions;
            if (expansions > max_expansions_) return {};
            if (cur.i == goal.i && cur.j == goal.j) {
                std::vector<IJ> path;
                STNode c = {cur.i, cur.j, cur.t};
                while (c.i != -1) {
                    path.push_back({c.i, c.j});
                    c = came_from[c];
                }
                std::reverse(path.begin(), path.end());
                return path;
            }
            if (cur.t >= dynamic_cap) continue;
            int nt = cur.t + 1;

            for (int d = 0; d < 9; ++d) {
                int ni = cur.i + DI[d], nj = cur.j + DJ[d];
                if (d == 0) { ni = cur.i; nj = cur.j; }
                else if (!p->is_free(ni, nj)) continue;

                if (d > 0) {
                    bool diag = (DI[d] != 0 && DJ[d] != 0);
                    if (diag && (!p->is_free(cur.i + DI[d], cur.j) ||
                                 !p->is_free(cur.i, cur.j + DJ[d])))
                        continue;
                }

                if (!is_vertex_free(ni, nj, nt, robot)) continue;
                if (!is_edge_free(cur.i, cur.j, ni, nj, cur.t, robot)) continue;

                double step_cost = (d == 0) ? 1.0 :
                    ((DI[d] != 0 && DJ[d] != 0) ? 1.4142135623730951 : 1.0);
                double new_g = cur.g + step_cost;
                STNode nb = {ni, nj, nt};
                auto it = g_score.find(nb);
                if (it == g_score.end() || new_g < it->second) {
                    g_score[nb] = new_g;
                    double h = std::hypot(ni - goal.i, nj - goal.j);
                    open.push({new_g + h, new_g, nt, ni, nj});
                    came_from[nb] = {cur.i, cur.j, cur.t};
                }
            }
        }
        return {};
    }
};
