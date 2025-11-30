import cv2
import time
import threading
import queue
import requests
import numpy as np  # <-- added so we can build a full-frame threshold image

# =========================
#  CAMERA INDEX CONFIG
# =========================
# Change these only if your devices are on different indices
IR_CAM_INDEX = 1        # IR webcam (marker detection)
LAPTOP_CAM_INDEX = 0    # Laptop webcam (selfie + HUD)

# =========================
#  IR / MARKER DETECTION TUNING
# =========================
# If you already have tuned values that work well,
# overwrite these numbers with your current ones.
BRIGHT_THRESH = 40      # threshold for "bright" IR pixels (0–255)
MIN_AREA = 2            # min contour area to consider as marker
MAX_AREA = 30           # max contour area to consider as marker

# If you use a Region Of Interest, you can tune these later.
# For now we use the full frame (0.0–1.0 means full extent).
ROI_TOP_FRAC = 0.15     # fraction of height from top
ROI_BOTTOM_FRAC = 0.55  # fraction of height from top
ROI_LEFT_FRAC = 0.30    # fraction of width from left
ROI_RIGHT_FRAC = 0.80   # fraction of width from left

# =========================
#  WINK / LIGHT LOGIC
# =========================
WINK_HOLD_TIME = 3.0      # seconds marker must stay VISIBLE to trigger toggle
WINK_IDLE_RESET = 0.80    # seconds after marker disappears to fully reset

# =========================
#  FONT / HUD STYLE
# =========================
FONT = cv2.FONT_HERSHEY_DUPLEX
HUD_MAIN_SCALE = 1.3
HUD_SMALL_SCALE = 1.0
HUD_THICK = 3


def putTextOutline(img, text, org, color_fg,
                   font=FONT, scale=HUD_MAIN_SCALE):
    """
    Draws outlined text for better readability on noisy backgrounds.
    """
    x, y = org
    # Big black outline
    cv2.putText(img, text, (x, y), font, scale, (0, 0, 0), 6, cv2.LINE_AA)
    # White border
    cv2.putText(img, text, (x, y), font, scale, (255, 255, 255), 4, cv2.LINE_AA)
    # Foreground
    cv2.putText(img, text, (x, y), font, scale, color_fg, 2, cv2.LINE_AA)


# =========================
#  GOVEE ROUTER API CONFIG
# =========================
GOVEE_API_KEY = "YOUR_API_KEY_HERE"
GOVEE_DEVICE_ID = "YOUR_DEVICE_ID_HERE"
GOVEE_SKU = "H6006"

# Create a config.py file locally containing:
# GOVEE_API_KEY = "actual_key"
# GOVEE_DEVICE_ID = "actual_device_id"


GOVEE_URL = "https://openapi.api.govee.com/router/api/v1/device/control"
GOVEE_HEADERS = {
    "Govee-API-Key": GOVEE_API_KEY,
    "Content-Type": "application/json",
}

# Background worker so HTTP doesn’t block camera loop
govee_queue: "queue.Queue[bool]" = queue.Queue()
light_is_on = False
light_lock = threading.Lock()


def govee_worker():
    """
    Drains a queue of on/off commands and sends the latest state to Govee.
    """
    global light_is_on
    while True:
        on = govee_queue.get()
        if on is None:
            # sentinel for shutdown (not used, but here for completeness)
            break

        with light_lock:
            light_is_on = on
            value = 1 if on else 0

        payload = {
            "requestId": "smartmakeup-blink",
            "payload": {
                "sku": GOVEE_SKU,
                "device": GOVEE_DEVICE_ID,
                "capability": {
                    "type": "devices.capabilities.on_off",
                    "instance": "powerSwitch",
                    "value": value,
                },
            },
        }

        try:
            print("Sending power command to Govee:", payload)
            r = requests.post(
                GOVEE_URL, json=payload, headers=GOVEE_HEADERS, timeout=3
            )
            print("Govee response:", r.status_code, r.text)
        except Exception as e:
            print("Govee error:", e)


def govee_set_power_async(on: bool):
    """
    Queue a power state change (True=ON, False=OFF).
    """
    try:
        govee_queue.put_nowait(on)
    except queue.Full:
        # In practice queue is unbounded; this is just defensive.
        pass


# =========================
#  MAIN
# =========================
def main():
    global light_is_on

    # Start Govee worker thread
    worker = threading.Thread(target=govee_worker, daemon=True)
    worker.start()

    # --- Open cameras ---
    cap_ir = cv2.VideoCapture(IR_CAM_INDEX, cv2.CAP_DSHOW)
    if not cap_ir.isOpened():
        print("Error: Could not open IR camera at index", IR_CAM_INDEX)
        return

    cap_lap = cv2.VideoCapture(LAPTOP_CAM_INDEX, cv2.CAP_DSHOW)
    if not cap_lap.isOpened():
        print("Error: Could not open laptop camera at index", LAPTOP_CAM_INDEX)
        cap_ir.release()
        return

    print("Camera opened. Press 'q' to quit, 'f' to force light OFF / reset wink.")
    print("Using Govee headers:", GOVEE_HEADERS)
    print("Using Govee URL:", GOVEE_URL)

    # Wink state
    wink_active = False
    wink_start_time = None
    wink_triggered_this_hold = False
    last_marker_time = 0.0

    # -------------------------
    #  Main processing loop
    # -------------------------
    while True:
        now = time.time()

        # ======================
        #  IR CAMERA / MARKER
        # ======================
        ret_ir, frame_ir = cap_ir.read()
        if not ret_ir:
            print("IR frame grab failed.")
            break

        h_ir, w_ir = frame_ir.shape[:2]

        # Define ROI in pixel coordinates
        roi_top = int(ROI_TOP_FRAC * h_ir)
        roi_bottom = int(ROI_BOTTOM_FRAC * h_ir)
        roi_left = int(ROI_LEFT_FRAC * w_ir)
        roi_right = int(ROI_RIGHT_FRAC * w_ir)

        roi = frame_ir[roi_top:roi_bottom, roi_left:roi_right]

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        gray_blur = cv2.GaussianBlur(gray, (5, 5), 0)

        _, thresh = cv2.threshold(
            gray_blur, BRIGHT_THRESH, 255, cv2.THRESH_BINARY
        )

        contours, _ = cv2.findContours(
            thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        marker_visible = False
        best_contour = None
        best_area = 0.0
        best_center = None

        for c in contours:
            area = cv2.contourArea(c)
            if area < MIN_AREA or area > MAX_AREA:
                continue

            if area > best_area:
                best_area = area
                best_contour = c
                M = cv2.moments(c)
                if M["m00"] != 0:
                    cx = int(M["m10"] / M["m00"])
                    cy = int(M["m01"] / M["m00"])
                    best_center = (cx, cy)

        # Draw visualisation on full IR frame
        vis_ir = frame_ir.copy()

        if best_contour is not None and best_center is not None:
            marker_visible = True
            last_marker_time = now

            cx, cy = best_center
            # Convert ROI coords back to full-frame coords
            cx_full = cx + roi_left
            cy_full = cy + roi_top
            x, y, w, h = cv2.boundingRect(best_contour)
            cv2.rectangle(
                vis_ir,
                (x + roi_left, y + roi_top),
                (x + roi_left + w, y + roi_top + h),
                (0, 255, 0),
                2,
            )
            cv2.putText(
                vis_ir,
                "MARKER",
                (cx_full + 10, cy_full - 10),
                FONT,
                0.9,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )

        # ---------- FULL-FRAME THRESHOLD FOR OBS ----------
        # Build a full-size blank image and paste the ROI threshold into it
        thresh_full = np.zeros((h_ir, w_ir), dtype=np.uint8)
        thresh_full[roi_top:roi_bottom, roi_left:roi_right] = thresh
        # ---------------------------------------------------

        # These names match your OBS window captures
        cv2.imshow("Threshold", thresh_full)
        cv2.imshow("Smart Makeup - IR Blink Detection", vis_ir)

        # ======================
        #  WINK STATE MACHINE
        # ======================
        # NOTE: In your setup, marker is VISIBLE when the eye is CLOSED
        # (reflector on eyelid). So "holding a wink" = marker_visible True.

        elapsed = 0.0

        if marker_visible:
            if not wink_active:
                wink_active = True
                wink_start_time = now
                wink_triggered_this_hold = False
            else:
                elapsed = now - wink_start_time
                if (not wink_triggered_this_hold) and elapsed >= WINK_HOLD_TIME:
                    # Toggle light
                    with light_lock:
                        new_state = not light_is_on
                    light_is_on = new_state
                    govee_set_power_async(new_state)

                    state_str = "ON" if new_state else "OFF"
                    print(f"WINK LOCK – TOGGLED LIGHT {state_str}")

                    wink_triggered_this_hold = True
        else:
            # No marker: check if we should fully reset based on idle time
            if wink_active and wink_start_time is not None:
                elapsed = now - wink_start_time
            if now - last_marker_time > WINK_IDLE_RESET:
                wink_active = False
                wink_start_time = None
                wink_triggered_this_hold = False
                elapsed = 0.0

        # ======================
        #  LAPTOP CAM + HUD
        # ======================
        ret_lap, frame_lap = cap_lap.read()
        if not ret_lap:
            print("Laptop frame grab failed.")
            break

        h_lap, w_lap = frame_lap.shape[:2]

        # ----- Centered WINK CHARGE title -----
        title_text = "WINK CHARGE"
        title_scale = 1.1  # sized to fit in TikTok frame

        (title_w, title_h), _ = cv2.getTextSize(
            title_text, FONT, title_scale, 2
        )
        # vertical position where old "??? READY F" was
        title_y = int(h_lap * 0.18) - 25
        title_x = (w_lap - title_w) // 2

        putTextOutline(
            frame_lap,
            title_text,
            (title_x, title_y),
            (255, 255, 0),
            scale=title_scale,
        )

        # ----- Charge bar centered under title -----
        bar_height = 14
        bar_width = int(w_lap * 0.80)              # long, but leaves margins
        bar_margin_x = (w_lap - bar_width) // 2
        bar_x1 = bar_margin_x
        bar_x2 = bar_x1 + bar_width

        bar_y = int(h_lap * 0.18)

        # Background bar (outline)
        cv2.rectangle(
            frame_lap,
            (bar_x1, bar_y),
            (bar_x2, bar_y + bar_height),
            (60, 60, 0),
            2,
        )

        # Progress fill
        progress = 0.0
        if wink_active and wink_start_time is not None:
            elapsed = now - wink_start_time
            progress = max(0.0, min(1.0, elapsed / WINK_HOLD_TIME))

        if progress > 0:
            fill_x2 = int(bar_x1 + bar_width * progress)
            cv2.rectangle(
                frame_lap,
                (bar_x1, bar_y),
                (fill_x2, bar_y + bar_height),
                (0, 255, 255),
                -1,
            )

        # ----- Timer text centered under the bar -----
        if wink_active and wink_start_time is not None:
            elapsed = now - wink_start_time
        else:
            elapsed = 0.0

        time_str = f"{elapsed:0.1f}s / {WINK_HOLD_TIME:0.1f}s"
        time_scale = 0.9

        (time_w, time_h), _ = cv2.getTextSize(
            time_str, FONT, time_scale, 2
        )

        time_x = (w_lap - time_w) // 2
        time_y = bar_y + bar_height + 30

        putTextOutline(
            frame_lap,
            time_str,
            (time_x, time_y),
            (255, 255, 255),
            scale=time_scale,
        )

        # ----- Light status at bottom -----
        status_text = f"LIGHT: {'ON' if light_is_on else 'OFF'}"
        status_color = (0, 255, 0) if light_is_on else (0, 0, 255)

        putTextOutline(
            frame_lap,
            status_text,
            (int(w_lap * 0.05), h_lap - 40),
            status_color,
            scale=HUD_MAIN_SCALE,
        )

        cv2.imshow("Laptop Camera – WINK HUD", frame_lap)

        # ======================
        #  KEYBOARD HANDLING
        # ======================
        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            break

        if key == ord("f") or key == ord("F"):
            # Force light OFF and reset wink state
            with light_lock:
                light_is_on = False
            govee_set_power_async(False)

            wink_active = False
            wink_start_time = None
            wink_triggered_this_hold = False
            last_marker_time = 0.0

            print("Hotkey 'F' pressed – forced LIGHT OFF and reset wink state.")

    # Cleanup
    cap_ir.release()
    cap_lap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
