cmake_minimum_required(VERSION 3.21)
project(remote_mcu_bridge C)
set(CMAKE_C_STANDARD 11)
add_compile_options(-Wall -Wextra -Werror -pedantic)
enable_testing()
find_package(Python3 COMPONENTS Interpreter REQUIRED)
add_library(rom_core rom/rom.c rom/devices.c)
target_include_directories(rom_core PUBLIC rom)
set_target_properties(rom_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
add_library(rombridge SHARED rom/fake_rom.c rom/native_bridge.c)
target_link_libraries(rombridge rom_core)
add_executable(rom_unit rom/test_rom.c rom/fake_rom.c)
target_link_libraries(rom_unit rom_core)
add_test(NAME rom_unit COMMAND rom_unit)
add_library(resident_core core/update.c core/supervisor.c)
target_include_directories(resident_core PUBLIC include core/include)
add_executable(portable_core tests/test_portable_core.c)
target_link_libraries(portable_core resident_core)
add_test(NAME portable_core COMMAND portable_core)
add_executable(supervisor_core esp32_rom/tests/test_supervisor_core.c)
target_link_libraries(supervisor_core resident_core rom_core)
add_test(NAME supervisor_core COMMAND supervisor_core)
add_test(NAME registry_contract COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_registry.py)
add_test(NAME esp_structure COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/esp32_rom/tests/static_checks.py)
add_executable(spi_workflow_native esp32_rom/module/logic/spi_diagnostic.c esp32_rom/tests/test_spi_workflow.c)
target_include_directories(spi_workflow_native PRIVATE esp32_rom/module/logic)
target_compile_options(spi_workflow_native PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
target_link_options(spi_workflow_native PRIVATE -fsanitize=address,undefined)
add_test(NAME spi_workflow_native COMMAND spi_workflow_native)
foreach(pair IN ITEMS
    "generic_network|esp32_rom/tests/test_generic_network.py"
    "service_watchdog|tests/test_service_watchdog.py"
    "management_fault_client|tests/test_management_fault_client.py"
    "exact_deadline|tests/test_exact_deadline.py"
    "supervisor_deadline|tests/test_supervisor_deadline.py"
    "service_abi|tests/test_service_abi.py"
    "rom_e2e|tests/rom_e2e.py"
    "ramtarget_unit|tests/test_ramtarget.py"
    "supervisor_startup|esp32_rom/tests/test_supervisor_startup.py"
    "uart_trace_host|esp32_rom/tests/test_uart_trace.py"
    "wifi_streams_host|esp32_rom/tests/test_streams.py"
    "spi_helpers_native|esp32_rom/tests/test_spi_module.py"
    "spi_backend_native|esp32_rom/tests/test_spi_backend.py"
    "module_format|esp32_rom/tests/test_module_format.py"
    "supervisor_contracts|esp32_rom/tests/test_supervisor.py"
    "supervisor_protocol|esp32_rom/tests/test_supervisor_protocol.py"
    "client_streams|tools/test_wifidebug.py"
    "discovery|tests/test_discovery.py"
    "probe_cli|tests/test_probe_cli.py")
    string(REPLACE "|" ";" fields "${pair}")
    list(GET fields 0 name)
    list(GET fields 1 script)
    add_test(NAME ${name} COMMAND ${CMAKE_COMMAND} -E env "RMB_NATIVE_LIBRARY=$<TARGET_FILE:rombridge>" ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/${script})
    set_tests_properties(${name} PROPERTIES TIMEOUT 60 LABELS native)
endforeach()
add_test(NAME spi_report_formatter COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/esp32_rom/tests/test_spi_report.py)
set_tests_properties(spi_report_formatter PROPERTIES TIMEOUT 60 LABELS native)
add_test(NAME spi_pad_request COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/esp32_rom/tests/test_spi_pad_request.py)
add_test(NAME hello_service_native COMMAND sh ${CMAKE_CURRENT_SOURCE_DIR}/examples/services/hello/test.sh)
set_tests_properties(spi_pad_request hello_service_native PROPERTIES TIMEOUT 60 LABELS native)
add_test(NAME cli_token_file COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_cli_token_file.py)
set_tests_properties(cli_token_file PROPERTIES TIMEOUT 30 LABELS native)
add_test(NAME build_config COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_build_config.py)
set_tests_properties(build_config PROPERTIES TIMEOUT 90 LABELS native)
