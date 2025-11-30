# SmartMakeup
Smart Makeup is a novel hands-free control system using the human face as the interface and IR-reflective cosmetics as the tool. This repo contains the Wink Charge Demo: IR eyelid marker detection + OpenCV HUD + Govee Router API toggle.
# Smart Makeup – IR Wink Charge Demo

> Prototype “wink to toggle” controller using an IR eyelid marker, dual webcams,  
> and a Govee Wi-Fi light. Designed to be recorded in a vertical (TikTok-style) frame.

This repository contains a Python / OpenCV demo that turns a **held wink** into a  
reliable **on/off switch** for a smart light.

- The **IR camera** tracks a small reflective marker on your eyelid and detects when the eye is closed.
- The **laptop camera** renders a **WINK CHARGE** HUD with:
  - A progress bar that fills while you hold the wink
  - A timer (e.g., `2.3s / 3.0s`)
  - A big **LIGHT: ON / OFF** status banner
- When the wink has been held long enough, the script toggles a **Govee light** via their Router API.

The goal of this demo is to show a **hands-free, glance-free control channel** that could later be
wrapped in **Smart Makeup™** products or integrated into **IR safety glasses**, GGEE middleware, and industrial UX.

---

## Demo Video

> _“Hold wink to charge → bar fills → light toggles – in one continuous shot.”_

(You can link your TikTok / Loom / MP4 here.)

---

## Features

- **Dual camera pipeline**
  - IR camera for robust marker detection
  - Laptop camera for human-friendly HUD overlay
- **Wink charge mechanic**
  - Hold wink for `WINK_HOLD_TIME` seconds to toggle
  - Idle timeout automatically resets the charge
- **Smart light integration (Govee)**
  - Uses the Router API to toggle a specific SKU/device
  - Background worker thread so HTTP never blocks video
- **Hotkey controls**
  - `q` – quit
  - `f` / `F` – force light OFF and reset wink state
- **Streamer-ready**
  - Works cleanly with OBS:
    - Threshold window
    - “Smart Makeup – IR Blink Detection” window
    - “Laptop Camera – WINK HUD” window (for vertical canvas)

---

## Architecture

High-level flow:

1. **IR capture**
   - Capture frame from `IR_CAM_INDEX`
   - Crop to a configurable ROI around the eye
   - Convert to grayscale → blur → binary threshold
   - Find bright contours in a size band (`MIN_AREA`…`MAX_AREA`)
   - If a valid blob exists, treat **marker as visible** (eye closed)

2. **Wink state machine**

   - When marker first appears → start wink timer
   - While still visible → compute `elapsed = now - wink_start_time`
   - When `elapsed >= WINK_HOLD_TIME` and not yet triggered:
     - Toggle `light_is_on`
     - Queue a command to the Govee worker
   - When marker disappears and stays gone for `WINK_IDLE_RESET` seconds:
     - Reset wink state and timers

3. **Laptop HUD**

   - Capture frame from `LAPTOP_CAM_INDEX`
   - Draw centered **WINK CHARGE** title
   - Draw a long progress bar across the upper third of the frame
   - Draw timer text and bottom **LIGHT: ON / OFF** banner
   - Show in window: `Laptop Camera – WINK HUD`

4. **Govee worker**

   - Background thread drains a queue of `True/False` commands
   - Builds a Router API payload and POSTs to:

     ```text
     https://openapi.api.govee.com/router/api/v1/device/control
     ```

---

## Hardware & Dependencies

### Hardware

- PC / laptop with:
  - **IR webcam** (used for eyelid marker)
  - **Regular webcam** (used for HUD / selfie feed)
- **Reflective eyelid marker**
  - For now: a punch-out of 3M retroreflective tape (or similar) placed on the upper eyelid
- **Govee Wi-Fi light** compatible with the Router API  
  (tested with SKU `H6006` and a specific device ID)

### Software

- Python 3.9+ (3.10/3.11 should also work)
- Packages:
  - `opencv-python`
  - `numpy` (usually pulled in by OpenCV)
  - `requests`

Install dependencies:

```bash
pip install opencv-python numpy requests
