cmake_minimum_required(VERSION 3.16)
project(aerodyne_firmware C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
add_compile_options(-Wall -Wextra -Wpedantic -Werror -Wshadow -Wconversion -Wno-sign-conversion)

execute_process(COMMAND git rev-parse --short=12 HEAD
                WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
                OUTPUT_VARIABLE AERO_GIT_COMMIT OUTPUT_STRIP_TRAILING_WHITESPACE
                ERROR_QUIET)
if(NOT AERO_GIT_COMMIT)
  set(AERO_GIT_COMMIT "unknown")
endif()
string(TIMESTAMP AERO_BUILD_TIME "%Y-%m-%dT%H:%M:%SZ" UTC)

# Portable flight-software core: pure logic, no RTOS or hardware dependencies.
set(CORE_SOURCES src/aero_fsm.c src/aero_kalman.c src/aero_telemetry.c src/aero_sensor.c src/aero_app.c
                 src/aero_version.c)

add_library(aerodyne_core STATIC ${CORE_SOURCES})
target_include_directories(aerodyne_core PUBLIC include)
target_compile_definitions(aerodyne_core PRIVATE
  AERO_FW_COMMIT="${AERO_GIT_COMMIT}" AERO_FW_BUILD_TIME="${AERO_BUILD_TIME}")
target_link_libraries(aerodyne_core PUBLIC m)

# Shared library used by the Python SIL harness (ctypes) to run the real C code.
add_library(aerodyne_fsw SHARED ${CORE_SOURCES})
target_include_directories(aerodyne_fsw PUBLIC include)
target_compile_definitions(aerodyne_fsw PRIVATE
  AERO_FW_COMMIT="${AERO_GIT_COMMIT}" AERO_FW_BUILD_TIME="${AERO_BUILD_TIME}")
target_link_libraries(aerodyne_fsw PUBLIC m)

enable_testing()
add_executable(test_firmware tests/test_firmware.c)
target_link_libraries(test_firmware aerodyne_core)
add_test(NAME firmware_unit_tests COMMAND test_firmware)
