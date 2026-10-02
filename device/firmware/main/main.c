/* ESP32-S3 device protocol core. Hardware is supplied by miko_board.h.
 * See ../../README.md before provisioning or enabling the LAN listener. */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cJSON.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "mbedtls/md.h"
#include "miko_board.h"

#define RX_MAX 32768
#define MIC_SAMPLES 480
#define WS_WAIT pdMS_TO_TICKS(1000)
#define MSG_LINK_DOWN 0xff
static const char *TAG = "miko_device";
static miko_board_config_t s_config;
static esp_websocket_client_handle_t s_ws;
static QueueHandle_t s_inbox;
static bool s_fatal;
static bool s_authenticated;
static bool s_ready;
static bool s_recording;
static bool s_suppress_output;
static uint32_t s_uplink_sequence;
static uint32_t s_downlink_sequence;
static char s_pending_audio_done[8][65];
static size_t s_pending_done_count;
static char s_heard_items[8][65];
static uint8_t s_heard_content_indices[8];
static size_t s_heard_item_count;
static char s_camera_request[37];
static TickType_t s_camera_deadline;
static uint8_t *s_rx_data;
static size_t s_rx_total;
static uint8_t s_rx_opcode;
static const char HEX[] = "0123456789abcdef";

typedef struct {
    uint8_t opcode;
    size_t length;
    uint8_t *data;
} device_msg_t;

static void clear_playback(void)
{
    miko_board_speaker_clear();
    s_pending_done_count = 0;
    s_heard_item_count = 0;
}

static void queue_link_down(void)
{
    device_msg_t msg = {.opcode = MSG_LINK_DOWN};
    if (xQueueSend(s_inbox, &msg, 0) != pdTRUE) s_fatal = true;
}

static void ws_event(void *arg, esp_event_base_t base, int32_t id, void *event_data)
{
    (void)arg; (void)base;
    if (id == WEBSOCKET_EVENT_DISCONNECTED || id == WEBSOCKET_EVENT_CLOSED) {
        free(s_rx_data); s_rx_data = NULL; s_rx_total = 0;
        queue_link_down();
        return;
    }
    if (id != WEBSOCKET_EVENT_DATA) return;
    const esp_websocket_event_data_t *event = event_data;
    if (!event || event->payload_len <= 0 || event->data_len <= 0) return;
    if (event->payload_len > RX_MAX || event->payload_offset < 0 ||
        event->payload_offset > event->payload_len ||
        event->data_len > event->payload_len - event->payload_offset) {
        s_fatal = true; return;
    }
    if (event->payload_offset == 0) {
        free(s_rx_data);
        s_rx_data = malloc((size_t)event->payload_len);
        if (!s_rx_data) { s_fatal = true; return; }
        s_rx_total = (size_t)event->payload_len;
        s_rx_opcode = event->op_code;
    }
    if (!s_rx_data || s_rx_total != (size_t)event->payload_len) { s_fatal = true; return; }
    memcpy(s_rx_data + event->payload_offset, event->data_ptr, (size_t)event->data_len);
    if (event->payload_offset + event->data_len == event->payload_len) {
        device_msg_t msg = {.opcode = s_rx_opcode, .length = s_rx_total, .data = s_rx_data};
        s_rx_data = NULL; s_rx_total = 0;
        if (xQueueSend(s_inbox, &msg, 0) != pdTRUE) {
            free(msg.data); s_fatal = true;
        }
    }
}

static bool ws_send_text(const char *text)
{
    if (!esp_websocket_client_is_connected(s_ws)) return false;
    size_t size = strlen(text);
    return size <= RX_MAX && esp_websocket_client_send_text(s_ws, text, (int)size, WS_WAIT) == (int)size;
}

static bool ws_send_json(cJSON *object)
{
    if (!object) return false;
    char *text = cJSON_PrintUnformatted(object);
    cJSON_Delete(object);
    if (!text) return false;
    bool ok = ws_send_text(text);
    free(text);
    return ok;
}

static bool ws_send_type(const char *type)
{
    cJSON *object = cJSON_CreateObject();
    cJSON_AddStringToObject(object, "type", type);
    return ws_send_json(object);
}

static bool send_local_interrupt(void)
{
    /* Take the hardware cursor before flushing DMA. Without a trustworthy
     * cursor, truncate from 0 ms so unheard assistant audio cannot become
     * conversation context or count as completed playback. */
    miko_playback_cursor_t cursor = {0};
    bool measured = miko_board_playback_cursor(&cursor);
    char unplayed[8][65];
    size_t unplayed_count = s_heard_item_count;
    memcpy(unplayed, s_heard_items, sizeof unplayed);
    char item_id[65] = {0};
    uint8_t content_index = 0;
    uint32_t played_ms = 0;
    if (measured && memchr(cursor.item_id, '\0', sizeof cursor.item_id) &&
        cursor.item_id[0] && cursor.played_ms <= 600000) {
        for (size_t i = 0; i < unplayed_count; ++i) {
            if (strcmp(cursor.item_id, unplayed[i]) == 0) {
                snprintf(item_id, sizeof item_id, "%s", cursor.item_id);
                content_index = cursor.content_index;
                played_ms = cursor.played_ms;
                break;
            }
        }
    }
    if (!item_id[0] && unplayed_count) {
        snprintf(item_id, sizeof item_id, "%s", unplayed[0]);
        content_index = s_heard_content_indices[0];
    }
    clear_playback();  /* local hardware stops before any network round trip */
    s_suppress_output = true;
    cJSON *event = cJSON_CreateObject();
    cJSON_AddStringToObject(event, "type", "interrupt");
    cJSON_AddStringToObject(event, "item_id", item_id);
    cJSON_AddNumberToObject(event, "content_index", content_index);
    cJSON_AddNumberToObject(event, "audio_end_ms", played_ms);
    cJSON_AddNumberToObject(event, "played_ms", played_ms);
    cJSON *other_items = cJSON_AddArrayToObject(event, "unplayed_item_ids");
    for (size_t i = 0; i < unplayed_count; ++i)
        if (strcmp(unplayed[i], item_id) != 0)
            cJSON_AddItemToArray(other_items, cJSON_CreateString(unplayed[i]));
    return ws_send_json(event);
}

static bool is_hex(const char *text, size_t length)
{
    if (!text || strlen(text) != length) return false;
    for (size_t i = 0; i < length; ++i)
        if (!((text[i] >= '0' && text[i] <= '9') || (text[i] >= 'a' && text[i] <= 'f'))) return false;
    return true;
}

static char hex_digit(uint8_t value) { return HEX[value & 15]; }

static bool authenticate(const char *server_nonce)
{
    if (!is_hex(server_nonce, 32)) return false;
    uint8_t random_bytes[16];
    char client_nonce[33];
    esp_fill_random(random_bytes, sizeof random_bytes);
    for (size_t i = 0; i < sizeof random_bytes; ++i) {
        client_nonce[2*i] = hex_digit(random_bytes[i] >> 4);
        client_nonce[2*i+1] = hex_digit(random_bytes[i]);
    }
    client_nonce[32] = '\0';
    char canonical[128];
    int used = snprintf(canonical, sizeof canonical, "MIKO-DEVICE-V1\n%s\n%s\n%s",
                        s_config.device_id, server_nonce, client_nonce);
    if (used < 0 || used >= (int)sizeof canonical) return false;
    const mbedtls_md_info_t *sha = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    uint8_t proof_bytes[32];
    if (!sha || mbedtls_md_hmac(sha, s_config.pairing_secret, 32,
                               (const uint8_t *)canonical, (size_t)used, proof_bytes) != 0) return false;
    char proof[65];
    for (size_t i = 0; i < 32; ++i) {
        proof[2*i] = hex_digit(proof_bytes[i] >> 4);
        proof[2*i+1] = hex_digit(proof_bytes[i]);
    }
    proof[64] = '\0';
    cJSON *object = cJSON_CreateObject();
    cJSON_AddStringToObject(object, "type", "authenticate");
    cJSON_AddStringToObject(object, "device_id", s_config.device_id);
    cJSON_AddStringToObject(object, "client_nonce", client_nonce);
    cJSON_AddStringToObject(object, "proof", proof);
    cJSON *caps = cJSON_AddArrayToObject(object, "capabilities");
    cJSON_AddItemToArray(caps, cJSON_CreateString("audio"));
    cJSON_AddItemToArray(caps, cJSON_CreateString("display"));
    cJSON_AddItemToArray(caps, cJSON_CreateString("gesture"));
    if (s_config.camera_enabled) cJSON_AddItemToArray(caps, cJSON_CreateString("camera"));
    memset(proof_bytes, 0, sizeof proof_bytes);
    memset(canonical, 0, sizeof canonical);
    return ws_send_json(object);
}

static bool parse_uuid(const char *text, uint8_t bytes[16])
{
    if (!text || strlen(text) != 36) return false;
    size_t out = 0;
    for (size_t i = 0; i < 36; ) {
        if (i == 8 || i == 13 || i == 18 || i == 23) { if (text[i++] != '-') return false; continue; }
        char a = text[i++], b = text[i++];
        const char *ap = strchr(HEX, a);
        const char *bp = strchr(HEX, b);
        if (!ap || !bp || out >= 16) return false;
        bytes[out++] = (uint8_t)(((ap - HEX) << 4) | (bp - HEX));
    }
    return out == 16;
}

static void reset_connection(void)
{
    s_authenticated = false;
    s_ready = false;
    s_recording = false;
    s_suppress_output = false;
    s_uplink_sequence = s_downlink_sequence = 0;
    s_camera_request[0] = '\0';
    clear_playback();
    miko_board_display("offline", "Reconnecting to Miko");
}

static void process_control(const uint8_t *data, size_t length)
{
    cJSON *object = cJSON_ParseWithLength((const char *)data, length);
    if (!object) { s_fatal = true; return; }
    const cJSON *type = cJSON_GetObjectItemCaseSensitive(object, "type");
    const char *kind = cJSON_IsString(type) ? type->valuestring : "";
    if (strcmp(kind, "challenge") == 0) {
        const cJSON *nonce = cJSON_GetObjectItemCaseSensitive(object, "server_nonce");
        if (!cJSON_IsString(nonce) || !authenticate(nonce->valuestring)) s_fatal = true;
    } else if (strcmp(kind, "accepted") == 0) {
        s_authenticated = true;
        cJSON *configure = cJSON_CreateObject();
        cJSON_AddStringToObject(configure, "type", "configure");
        cJSON_AddStringToObject(configure, "mode", s_config.hands_free ? "hands_free" : "ptt");
        if (!ws_send_json(configure)) s_fatal = true;
        miko_board_display("connecting", "Voice session opening");
    } else if (strcmp(kind, "ready") == 0) {
        s_ready = true;
        miko_board_display("ready", s_config.hands_free ? "Microphone available" : "Hold to speak");
    } else if (strcmp(kind, "transcript") == 0) {
        const cJSON *role = cJSON_GetObjectItemCaseSensitive(object, "role");
        const cJSON *text = cJSON_GetObjectItemCaseSensitive(object, "text");
        if (cJSON_IsString(role) && cJSON_IsString(text))
            miko_board_caption(role->valuestring, text->valuestring);
    } else if (strcmp(kind, "status") == 0) {
        const cJSON *status = cJSON_GetObjectItemCaseSensitive(object, "status");
        const cJSON *detail = cJSON_GetObjectItemCaseSensitive(object, "detail");
        miko_board_display(cJSON_IsString(status) ? status->valuestring : "idle",
                           cJSON_IsString(detail) ? detail->valuestring : "");
    } else if (strcmp(kind, "gesture") == 0) {
        const cJSON *emotion = cJSON_GetObjectItemCaseSensitive(object, "emotion");
        const cJSON *action = cJSON_GetObjectItemCaseSensitive(object, "action");
        miko_board_gesture(cJSON_IsString(emotion) ? emotion->valuestring : "neutral",
                           cJSON_IsString(action) ? action->valuestring : "idle");
    } else if (strcmp(kind, "audio_done") == 0) {
        const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, "item_id");
        if (!s_suppress_output && !s_recording && cJSON_IsString(item) && strlen(item->valuestring) <= 64) {
            bool heard = false;
            for (size_t i = 0; i < s_heard_item_count; ++i)
                if (strcmp(item->valuestring, s_heard_items[i]) == 0) heard = true;
            if (heard && s_pending_done_count < 8)
                snprintf(s_pending_audio_done[s_pending_done_count++], 65, "%s", item->valuestring);
        }
    } else if (strcmp(kind, "interrupted") == 0) {
        clear_playback();
        s_suppress_output = false;
    } else if (strcmp(kind, "camera_request") == 0) {
        const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, "request_id");
        const cJSON *expires = cJSON_GetObjectItemCaseSensitive(object, "expires_ms");
        uint8_t uuid_bytes[16];
        if (s_config.camera_enabled && cJSON_IsString(item) &&
            parse_uuid(item->valuestring, uuid_bytes) &&
            cJSON_IsNumber(expires) && expires->valueint > 0 && expires->valueint <= 10000) {
            snprintf(s_camera_request, sizeof s_camera_request, "%s", item->valuestring);
            s_camera_deadline = xTaskGetTickCount() + pdMS_TO_TICKS(expires->valueint);
            miko_board_camera_prompt((uint32_t)expires->valueint);
        }
    } else if (strcmp(kind, "error") == 0) {
        miko_board_display("error", "Voice service error");
    }
    cJSON_Delete(object);
}

static void process_audio(const uint8_t *data, size_t length)
{
    if (length < 10 || data[0] != 0x02 || data[1] == 0 || data[1] > 64 ||
        length < (size_t)7 + data[1] + 2) { s_fatal = true; return; }
    uint32_t sequence = ((uint32_t)data[3] << 24) | ((uint32_t)data[4] << 16) |
                        ((uint32_t)data[5] << 8) | data[6];
    if (sequence != s_downlink_sequence++) { s_fatal = true; return; }
    if (s_suppress_output || (s_recording && !s_config.hands_free)) return;
    size_t offset = (size_t)7 + data[1];
    if ((length - offset) & 1) { s_fatal = true; return; }
    char item_id[65];
    memcpy(item_id, data + 7, data[1]);
    item_id[data[1]] = '\0';
    bool known = false;
    for (size_t i = 0; i < s_heard_item_count; ++i)
        if (strcmp(item_id, s_heard_items[i]) == 0) known = true;
    if (!known) {
        if (s_heard_item_count == 8) { s_fatal = true; return; }
        snprintf(s_heard_items[s_heard_item_count], 65, "%s", item_id);
        s_heard_content_indices[s_heard_item_count] = data[2];
        s_heard_item_count++;
    }
    if (miko_board_speaker_enqueue(data + offset, length - offset) != ESP_OK)
        s_fatal = true;
}

static void send_mic(const int16_t *samples, size_t count)
{
    if (!count || count > MIC_SAMPLES) return;
    uint8_t frame[5 + MIC_SAMPLES * 2];
    uint32_t number = s_uplink_sequence++;
    frame[0] = 0x01;
    frame[1] = (uint8_t)(number >> 24); frame[2] = (uint8_t)(number >> 16);
    frame[3] = (uint8_t)(number >> 8); frame[4] = (uint8_t)number;
    memcpy(frame + 5, samples, count * 2);
    int length = (int)(5 + count * 2);
    if (esp_websocket_client_send_bin(s_ws, (const char *)frame, length, WS_WAIT) != length) s_fatal = true;
}

static void maybe_send_camera(void)
{
    if (!s_camera_request[0]) return;
    if ((int32_t)(xTaskGetTickCount() - s_camera_deadline) >= 0) {
        s_camera_request[0] = '\0'; return;
    }
    if (!miko_board_camera_confirmed()) return;
    uint8_t id[16], *jpeg = NULL;
    size_t size = 0;
    if (!parse_uuid(s_camera_request, id)) { s_camera_request[0] = '\0'; return; }
    if (miko_board_camera_capture(&jpeg, &size) != ESP_OK || !jpeg ||
        size < 4 || size > MIKO_JPEG_MAX || jpeg[0] != 0xff || jpeg[1] != 0xd8 ||
        jpeg[size-2] != 0xff || jpeg[size-1] != 0xd9 ||
        (int32_t)(xTaskGetTickCount() - s_camera_deadline) >= 0) {
        if (jpeg) miko_board_camera_discard(jpeg);
        s_camera_request[0] = '\0'; return;
    }
    uint8_t *frame = malloc(17 + size); /* use PSRAM allocator in a real board adapter */
    if (frame) {
        frame[0] = 0x03;
        memcpy(frame + 1, id, 16);
        memcpy(frame + 17, jpeg, size);
        if (esp_websocket_client_send_bin(s_ws, (const char *)frame, (int)(17 + size), WS_WAIT) != (int)(17 + size))
            s_fatal = true;
        free(frame);
    }
    miko_board_camera_discard(jpeg);
    s_camera_request[0] = '\0';
}

void app_main(void)
{
    memset(&s_config, 0, sizeof s_config);
    if (miko_board_init(&s_config) != ESP_OK || !s_config.pinned_cert_pem ||
        strncmp(s_config.wss_uri, "wss://", 6) != 0 || !s_config.device_id[0]) {
        ESP_LOGE(TAG, "Board adapter/provisioning is incomplete; device network disabled");
        return;
    }
    s_inbox = xQueueCreate(8, sizeof(device_msg_t));
    if (!s_inbox) return;
    const esp_websocket_client_config_t ws_config = {
        .uri = s_config.wss_uri,
        .cert_pem = s_config.pinned_cert_pem,
        .reconnect_timeout_ms = 3000,
        .buffer_size = 4096,
    };
    s_ws = esp_websocket_client_init(&ws_config);
    if (!s_ws) return;
    ESP_ERROR_CHECK(esp_websocket_register_events(s_ws, WEBSOCKET_EVENT_ANY, ws_event, NULL));
    ESP_ERROR_CHECK(esp_websocket_client_start(s_ws));
    int16_t samples[MIC_SAMPLES];
    for (;;) {
        device_msg_t msg;
        if (xQueueReceive(s_inbox, &msg, pdMS_TO_TICKS(10)) == pdTRUE) {
            if (msg.opcode == MSG_LINK_DOWN) reset_connection();
            else if (msg.opcode == 0x01) process_control(msg.data, msg.length);
            else if (msg.opcode == 0x02 && s_authenticated) process_audio(msg.data, msg.length);
            free(msg.data);
        }
        if (s_fatal) {
            s_fatal = false;
            reset_connection();
            esp_websocket_client_close(s_ws, WS_WAIT); /* outside event handler */
            while (xQueueReceive(s_inbox, &msg, 0) == pdTRUE) free(msg.data);
            vTaskDelay(pdMS_TO_TICKS(1000));
            if (esp_websocket_client_start(s_ws) != ESP_OK) {
                ESP_LOGE(TAG, "WebSocket restart failed");
                return;
            }
            continue;
        }
        if (!s_authenticated || !s_ready || !esp_websocket_client_is_connected(s_ws)) continue;
        bool capture = !miko_board_mic_muted() && (s_config.hands_free || miko_board_ptt_down());
        if (capture && !s_recording) {
            if (!s_config.hands_free && (!send_local_interrupt() || !ws_send_type("start")))
                s_fatal = true;
            s_recording = true;
        } else if (!capture && s_recording) {
            if (!s_config.hands_free && !ws_send_type("stop")) s_fatal = true;
            s_recording = false;
        }
        if (s_pending_done_count && miko_board_speaker_idle()) {
            cJSON *ack = cJSON_CreateObject();
            cJSON_AddStringToObject(ack, "type", "playback");
            cJSON_AddStringToObject(ack, "item_id", s_pending_audio_done[0]);
            cJSON_AddBoolToObject(ack, "finished", true);
            if (!ws_send_json(ack)) s_fatal = true;
            for (size_t i = 0; i < s_heard_item_count; ++i) {
                if (strcmp(s_heard_items[i], s_pending_audio_done[0]) == 0) {
                    memmove(s_heard_items + i, s_heard_items + i + 1,
                            (--s_heard_item_count - i) * 65);
                    memmove(s_heard_content_indices + i, s_heard_content_indices + i + 1,
                            s_heard_item_count - i);
                    break;
                }
            }
            memmove(s_pending_audio_done, s_pending_audio_done + 1, (--s_pending_done_count) * 65);
        }
        maybe_send_camera();
        if (capture) {
            size_t count = MIC_SAMPLES;
            if (miko_board_mic_read(samples, &count, 20) == ESP_OK && count <= MIC_SAMPLES)
                send_mic(samples, count);
        }
    }
}
