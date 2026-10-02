# Miko display frames

These 240×280 RGB PNGs are pre-rendered views of the original orange Miko model. An ESP display adapter can select a frame from `manifest.json` when the host sends `status` or `gesture` events. The ESP does not run Godot or render the GLB.

`idle`, `listening`, `speaking`, `happy`, and `sleep` are the main states. The three `_blink` files are short optional frames. The bottom 30 pixels are the suggested place for a short caption.

To regenerate on a Windows host with Godot 4.7.2 Mono, run the console executable from this project with `--path <miko-3d directory> --script res://export_esp_avatar.gd`. The exporter removes the controller script before loading the scene, so it does not start the microphone, network, or desktop UI.

These images are display assets only. Board specific decoding, memory sizing, pin wiring, and frame timing remain to be verified on physical hardware.
