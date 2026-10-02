idf_component_register(SRCS "main.c" "miko_board_stub.c"
                       INCLUDE_DIRS "."
                       REQUIRES esp_websocket_client mbedtls json)
