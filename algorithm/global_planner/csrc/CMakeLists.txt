cmake_minimum_required(VERSION 3.14)
project(_planner_cpp LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

find_package(pybind11 REQUIRED)

pybind11_add_module(_planner_cpp bindings.cpp)
target_include_directories(_planner_cpp PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_compile_options(_planner_cpp PRIVATE -O3 -march=native -DNDEBUG)

install(TARGETS _planner_cpp DESTINATION .)
