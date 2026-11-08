idf_component_register(SRCS "service.c" "module_runtime.c" "generic_network.c" "../../core/update.c" "../../core/supervisor.c"
    INCLUDE_DIRS "." "../../include"
    REQUIRES esp_app_format driver esp_wifi esp_event esp_netif esp_timer esp_partition nvs_flash json mbedtls)
target_compile_options(${COMPONENT_LIB} PRIVATE -Wno-error=misleading-indentation)

# Stable generic runtime exports used by independently linked IDF drivers.
# No UART/GPIO wrapper and no second RTOS/newlib instance in a module.
target_link_libraries(${COMPONENT_LIB} INTERFACE
 "-Wl,-u,vSemaphoreDeleteWithCaps" "-Wl,-u,vQueueDeleteWithCaps"
 "-Wl,-u,xSemaphoreCreateGenericWithCaps" "-Wl,-u,xQueueCreateWithCaps"
 "-Wl,-u,uxQueueSpacesAvailable" "-Wl,-u,__atomic_fetch_and_8")
