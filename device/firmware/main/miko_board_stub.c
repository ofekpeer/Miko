#include "miko_board.h"

/* Deliberately inert board adapter. Replace this file with code for the
 * precise display controller, pin map, I2S codec, camera and consent button. */
esp_err_t miko_board_init(miko_board_config_t *config) { (void)config; return ESP_ERR_NOT_SUPPORTED; }
bool miko_board_ptt_down(void) { return false; }
bool miko_board_mic_muted(void) { return true; }
esp_err_t miko_board_mic_read(int16_t *p, size_t *n, uint32_t t) { (void)p; (void)n; (void)t; return ESP_ERR_NOT_SUPPORTED; }
esp_err_t miko_board_speaker_enqueue(const uint8_t *p, size_t n) { (void)p; (void)n; return ESP_ERR_NOT_SUPPORTED; }
bool miko_board_speaker_idle(void) { return true; }
void miko_board_speaker_clear(void) {}
bool miko_board_playback_cursor(miko_playback_cursor_t *c) { (void)c; return false; }
void miko_board_display(const char *s, const char *d) { (void)s; (void)d; }
void miko_board_caption(const char *r, const char *t) { (void)r; (void)t; }
void miko_board_gesture(const char *e, const char *a) { (void)e; (void)a; }
void miko_board_camera_prompt(uint32_t e) { (void)e; }
bool miko_board_camera_confirmed(void) { return false; }
esp_err_t miko_board_camera_capture(uint8_t **j, size_t *n) { (void)j; (void)n; return ESP_ERR_NOT_SUPPORTED; }
void miko_board_camera_discard(uint8_t *j) { (void)j; }
esp_err_t miko_board_vision_start(uint32_t f, uint32_t w, uint32_t h) { (void)f; (void)w; (void)h; return ESP_ERR_NOT_SUPPORTED; }
esp_err_t miko_board_vision_frame(uint8_t **j, size_t *n) { (void)j; (void)n; return ESP_ERR_NOT_FOUND; }
void miko_board_vision_discard(uint8_t *j) { (void)j; }
void miko_board_vision_stop(void) {}
