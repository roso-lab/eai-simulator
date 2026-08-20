cmake_minimum_required(VERSION 3.16)
project(eai_dbcbs VERSION 1.6.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

include(GNUInstallDirs)
find_package(Boost 1.58 REQUIRED COMPONENTS
  filesystem
  program_options
  serialization
  stacktrace_basic
  system
)
find_package(Eigen3 REQUIRED)
find_package(Threads REQUIRED)
find_package(fcl REQUIRED)
find_package(yaml-cpp REQUIRED)
find_path(CROCODDYL_INCLUDE_DIR crocoddyl/core/fwd.hpp REQUIRED)
find_library(CROCODDYL_LIBRARY NAMES crocoddyl REQUIRED)
set(CROCODDYL_INCLUDE_DIRS ${CROCODDYL_INCLUDE_DIR})
set(CROCODDYL_LIBRARIES ${CROCODDYL_LIBRARY})

# Build the OMPL revision shipped with db-CBS. Only the library source is
# vendored; upstream demos, bindings, documentation, and tests are excluded.
set(OMPL_ABI_VERSION 17)
set(OMPL_EXTENSION_MORSE OFF)
set(OMPL_EXTENSION_ODE OFF)
set(OMPL_EXTENSION_TRIANGLE OFF)
set(OMPL_HAVE_FLANN OFF)
set(OMPL_HAVE_SPOT OFF)
set(OMPL_HAVE_NUMPY OFF)
set(OMPL_LINK_LIBRARIES "")
add_subdirectory(ompl/ompl ${CMAKE_BINARY_DIR}/ompl)
add_library(ompl::ompl ALIAS ompl)
target_include_directories(ompl PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/ompl
  ${CMAKE_BINARY_DIR}
  ${EIGEN3_INCLUDE_DIR}
)

add_library(dynobench STATIC
  dynoplan/dynobench/src/robot_models.cpp
  dynoplan/dynobench/src/robot_models_base.cpp
  dynoplan/dynobench/src/motions.cpp
  dynoplan/dynobench/src/car.cpp
  dynoplan/dynobench/src/acrobot.cpp
  dynoplan/dynobench/src/quadrotor.cpp
  dynoplan/dynobench/src/unicycle1.cpp
  dynoplan/dynobench/src/unicycle2.cpp
  dynoplan/dynobench/src/planar_rotor.cpp
  dynoplan/dynobench/src/planar_rotor_pole.cpp
  dynoplan/dynobench/src/car2.cpp
  dynoplan/dynobench/src/integrator2_2d.cpp
  dynoplan/dynobench/src/joint_robot.cpp
  dynoplan/dynobench/src/integrator1_2d.cpp
)
add_library(dynobench::dynobench ALIAS dynobench)
target_include_directories(dynobench PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/dynoplan/dynobench/include
  ${CMAKE_CURRENT_SOURCE_DIR}/dynoplan/dynobench/deps/json/include
)
target_link_libraries(dynobench PUBLIC
  Eigen3::Eigen
  fcl
  yaml-cpp
  Boost::program_options
  Boost::serialization
  Boost::stacktrace_basic
  ${CMAKE_DL_LIBS}
)

add_library(dynoplan_optimization STATIC
  dynoplan/src/optimization/ocp.cpp
  dynoplan/src/optimization/options.cpp
  dynoplan/src/optimization/croco_models.cpp
  dynoplan/src/optimization/generate_ocp.cpp
  dynoplan/src/optimization/multirobot_optimization.cpp
)
target_include_directories(dynoplan_optimization PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/dynoplan/include
  ${CROCODDYL_INCLUDE_DIRS}
)
target_link_libraries(dynoplan_optimization PUBLIC
  dynobench::dynobench
  Eigen3::Eigen
  ${CROCODDYL_LIBRARIES}
  fcl
  yaml-cpp
  Boost::program_options
  Boost::serialization
)

add_library(dbcbs_robot_model STATIC src/robots.cpp)
target_include_directories(dbcbs_robot_model PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/src
  ${CMAKE_CURRENT_SOURCE_DIR}/ompl
  ${CMAKE_BINARY_DIR}
)
target_link_libraries(dbcbs_robot_model PUBLIC ompl::ompl fcl)

add_executable(db_cbs src/db_cbs.cpp)
target_include_directories(db_cbs PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/src
  ${CMAKE_CURRENT_SOURCE_DIR}/dynoplan/include
  ${CMAKE_CURRENT_SOURCE_DIR}/dynoplan/dynobench/include
  ${CMAKE_CURRENT_SOURCE_DIR}/dynoplan/dynobench/deps/json/include
  ${CMAKE_CURRENT_SOURCE_DIR}/ompl
  ${CMAKE_BINARY_DIR}
)
target_link_libraries(db_cbs PRIVATE
  dbcbs_robot_model
  dynoplan_optimization
  dynobench::dynobench
  ompl::ompl
  fcl
  yaml-cpp
  Boost::program_options
)

set_target_properties(db_cbs PROPERTIES
  BUILD_RPATH_USE_ORIGIN ON
  INSTALL_RPATH_USE_LINK_PATH ON
)
