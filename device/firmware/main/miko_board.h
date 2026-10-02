#pragma once

/* Implement these hooks for the exact ESP32-S3 board revision and pinout.
 * Keep the pairing key in protected NVS and the pinned server certificate in
 * provisioned storage. No OpenAI key, mail credential or model prompt belongs
 * on the device. All PCM is 24 kHz mono signed 16-bit little-endian. */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#define MIKO_JPEG_MAX 160000
#define MIKO_VISION_JPEG_MAX 24000

typedef struct {
    char device_id[33];
    char wss_uri[160];                 /* wss://<private-ip>:<port>/device/v1 */
    const char *pinned_cert_pem;      /* NUL-terminated PEM; TLS verification required */
    uint8_t pairing_secret[32];      /* read from provisioned NVS */
    bool camera_enabled;             /* false until owner explicitly provisions it */
    bool vision_enabled;             /* owner-provisioned perception stream (presence, waves) */
    bool hands_free;                  /* board must supply AEC and visible mic state */
} miko_board_config_t;

/* Connect to Wi-Fi, initialize 240x280 display, mic, speaker, button and
 * optional camera. Return ESP_ERR_NOT_SUPPORTED until the board adapter is
 * supplied. The network must be up before this returns ESP_OK. */
esp_err_t miko_board_init(miko_board_config_t *config);
bool miko_board_ptt_down(void);
bool miko_board_mic_muted(void);      /* physical mute switch or equivalent */
esp_err_t miko_board_mic_read(int16_t *samples, size_t *sample_count, uint32_t timeout_ms);
/* Copy bytes before returning. Return non-OK on queue overflow. Never discard
 * speech and then acknowledge it. */
esp_err_t miko_board_speaker_enqueue(const uint8_t *pcm_le, size_t byte_count);
bool miko_board_speaker_idle(void);  /* true only after the DAC has actually played all samples */
void miko_board_speaker_clear(void); /* flush DMA/ring buffer on barge-in */
typedef struct {
    char item_id[65];
    uint8_t content_index;
    uint32_t played_ms;             /* actual DAC position for this item/content */
} miko_playback_cursor_t;
/* Return false when hardware cannot identify the active item and actual
 * played position. The protocol will then report 0 ms conservatively. */
bool miko_board_playback_cursor(miko_playback_cursor_t *cursor);
void miko_board_display(const char *status, const char *detail);
void miko_board_caption(const char *role, const char *text);
/* action may be any miko_perform_action body action (wave, jump, walk_left,
 * dance, sit, ...) or an expression cue; animate what the screen can show. */
void miko_board_gesture(const char *emotion, const char *action);

/* Camera requires a fresh on-screen prompt and a local physical button press.
 * The server request alone must never call the sensor. Capture only after
 * miko_board_camera_confirmed() returns true for this one request, consuming
 * that confirmation. Clear any stale button press when showing a new prompt. */
void miko_board_camera_prompt(uint32_t expires_ms);
bool miko_board_camera_confirmed(void);
esp_err_t miko_board_camera_capture(uint8_t **jpeg, size_t *length);
void miko_board_camera_discard(uint8_t *jpeg);

/* Perception stream ("Miko sees you"): small, low-rate JPEG frames that the
 * server analyses in memory for presence, position and waves. Frames are never
 * stored on the device or server. Only while the server sent vision_stream
 * active=true (owner toggle) and vision_enabled was provisioned.
 * start: configure the sensor for about width x height at fps and turn ON a
 * visible camera indicator (LED or on-screen dot) for as long as it streams.
 * frame: non-blocking; return ESP_ERR_NOT_FOUND when no new frame is ready;
 * JPEG must be <= MIKO_VISION_JPEG_MAX bytes. stop: sensor off, indicator off. */
esp_err_t miko_board_vision_start(uint32_t fps, uint32_t width, uint32_t height);
esp_err_t miko_board_vision_frame(uint8_t **jpeg, size_t *length);
void miko_board_vision_discard(uint8_t *jpeg);
void miko_board_vision_stop(void);
