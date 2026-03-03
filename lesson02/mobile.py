import time
from io import BytesIO
from typing import List, Optional, Tuple

from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import TimeoutException

from PIL import Image
import pytesseract


# ================== CONFIG ==================
APPIUM_URL = "http://127.0.0.1:4723"
UDID = "ec33abab"  # máy thật
VOUCHER_CODE = "33SPFMOI60"
MAX_ATTEMPTS = 30

# Texts
BTN_ADD_EXACT = ["Add", "Apply"]
BTN_COMMIT_EXACT = ["Use Voucher", "OK"]  # bạn nói sẽ đổi sau, tạm giữ
PROMOTION_CODE_CONTAINS = ["Promotion Code"]

BTN_PLACE_ORDER_CONTAINS = ["Place Order"]
BTN_SUBMIT_EXACT = ["Submit Now", "Submit"]
ROW_ADD_VOUCHER_CONTAINS = ["Add Voucher"]

# Time windows
T_WAIT_ADD_BTN = 6.0
T_WAIT_COMMIT_BTN = 6.0
T_WAIT_PROMO_CODE = 10.0          # giảm để nhanh hơn (có thể tăng nếu mạng rất chậm)
T_SPAM_PLACE_TO_SUBMIT = 30.0

# FAIL popup OK heuristic
POPUP_OK_Y_MAX_RATIO = 0.75

# ===== You saved ROI (given by you) =====
BASE_W, BASE_H = 1080, 2397
ROI_X1, ROI_X2 = 14, 246
ROI_Y1, ROI_Y2 = 2098, 2204
ROI_PAD_X = 10
ROI_PAD_Y = 10

# OCR tuning: CHỈ OCR ít lần cho nhanh
OCR_MAX_CALLS = 3             # <= 3 lần / attempt
OCR_MIN_GAP_SEC = 0.45        # khoảng cách tối thiểu giữa các lần OCR
OCR_HARD_TIMEOUT = 4.0        # quá 4s mà vẫn không thấy you saved => coi như fail/timeout nhanh

# Place/Submit polling (100ms theo yêu cầu)
POLL_PLACE = 0.10

# Tesseract
TESSERACT_EXE = r"C:\Users\HUNG PHAM\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
pytesseract.pytesseract.tesseract_cmd = TESSERACT_EXE

# Tesseract config để nhanh hơn (single line + tắt dictionary)
TESS_CFG = "--psm 7 --oem 1 -l eng -c load_system_dawg=0 -c load_freq_dawg=0"
# ===========================================


def now() -> float:
    return time.monotonic()


def ui_text_is(t: str) -> str:
    return f'new UiSelector().text("{t}")'


def ui_text_contains(t: str) -> str:
    return f'new UiSelector().textContains("{t}")'


def safe_tap_center(driver, el):
    r = el.rect
    x = int(r["x"] + r["width"] / 2)
    y = int(r["y"] + r["height"] / 2)
    driver.execute_script("mobile: clickGesture", {"x": x, "y": y})


def tap_xy(driver, x: int, y: int):
    driver.execute_script("mobile: clickGesture", {"x": int(x), "y": int(y)})


def try_find_any_text(driver, texts: List[str], *, contains: bool) -> Optional[object]:
    for t in texts:
        sel = ui_text_contains(t) if contains else ui_text_is(t)
        els = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)
        if els:
            return els[0]
    return None


def wait_any_text(driver, texts: List[str], timeout: float, *, contains: bool, poll: float = 0.08) -> object:
    end = now() + timeout
    while now() < end:
        el = try_find_any_text(driver, texts, contains=contains)
        if el:
            return el
        time.sleep(poll)
    raise TimeoutException(f"Not found: {texts} (contains={contains}) in {timeout}s")


def screenshot_rgb(driver) -> Image.Image:
    png = driver.get_screenshot_as_png()
    return Image.open(BytesIO(png)).convert("RGB")


def _scaled_roi(img_w: int, img_h: int) -> Tuple[int, int, int, int]:
    sx = img_w / BASE_W
    sy = img_h / BASE_H

    x1 = int((ROI_X1 - ROI_PAD_X) * sx)
    x2 = int((ROI_X2 + ROI_PAD_X) * sx)
    y1 = int((ROI_Y1 - ROI_PAD_Y) * sy)
    y2 = int((ROI_Y2 + ROI_PAD_Y) * sy)

    x1 = max(0, min(img_w - 1, x1))
    x2 = max(1, min(img_w, x2))
    y1 = max(0, min(img_h - 1, y1))
    y2 = max(1, min(img_h, y2))
    if x2 <= x1:
        x2 = min(img_w, x1 + 1)
    if y2 <= y1:
        y2 = min(img_h, y1 + 1)
    return x1, y1, x2, y2


def _roi_has_text_fast(gray: Image.Image) -> bool:
    """
    Prefilter siêu nhẹ: nếu ROI gần như trắng trơn thì khỏi OCR (tiết kiệm thời gian).
    """
    # downsample nhanh
    small = gray.resize((max(1, gray.width // 3), max(1, gray.height // 3)))
    px = list(small.getdata())
    # đếm pixel "tối" (chữ)
    dark = sum(1 for p in px if p < 160)
    return dark > max(8, int(0.01 * len(px)))


def ocr_you_saved_present(driver) -> bool:
    img = screenshot_rgb(driver)
    w, h = img.size
    x1, y1, x2, y2 = _scaled_roi(w, h)

    roi = img.crop((x1, y1, x2, y2)).convert("L")

    # prefilter: không có chữ thì bỏ qua OCR để nhanh
    if not _roi_has_text_fast(roi):
        return False

    # threshold nhẹ
    roi = roi.point(lambda p: 255 if p > 185 else p)
    text = pytesseract.image_to_string(roi, config=TESS_CFG).strip().lower()

    # robust
    if "you saved" in text:
        return True
    if "saved" in text and ("you" in text or "ship" in text):
        return True
    return False


def _ok_center_y(el) -> float:
    r = el.rect
    return float(r["y"] + r["height"] / 2)


def click_fail_popup_ok(driver) -> bool:
    # system dialog OK
    sys_ok = driver.find_elements(AppiumBy.ID, "android:id/button1")
    if sys_ok:
        safe_tap_center(driver, sys_ok[0])
        return True

    ok_els = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, ui_text_is("OK"))
    if not ok_els:
        return False

    h = driver.get_window_size()["height"]
    popup = []
    for el in ok_els:
        cy = _ok_center_y(el)
        if cy < h * POPUP_OK_Y_MAX_RATIO:
            popup.append((cy, el))
    if popup:
        popup.sort(key=lambda x: x[0])
        safe_tap_center(driver, popup[0][1])
        return True
    return False


def promo_code_visible(driver) -> bool:
    return try_find_any_text(driver, PROMOTION_CODE_CONTAINS, contains=True) is not None


def open_voucher_from_confirm(driver):
    row = try_find_any_text(driver, ROW_ADD_VOUCHER_CONTAINS, contains=True)
    if row:
        safe_tap_center(driver, row)


def find_voucher_input(driver):
    candidates = driver.find_elements(
        AppiumBy.ANDROID_UIAUTOMATOR,
        'new UiSelector().className("android.widget.EditText")'
    )
    if not candidates:
        raise TimeoutException("Cannot find voucher EditText")
    for el in candidates:
        try:
            txt = (el.get_attribute("text") or "").lower()
            if "voucher" in txt:
                return el
        except Exception:
            pass
    return candidates[0]


def dismiss_keyboard(driver):
    try:
        driver.hide_keyboard()
        return
    except Exception:
        pass
    try:
        driver.press_keycode(4)
    except Exception:
        pass


def set_text_fast(driver, el, text: str):
    safe_tap_center(driver, el)
    try:
        el.clear()
    except Exception:
        pass
    # fastest for voucher codes
    try:
        driver.execute_script("mobile: shell", {"command": "input", "args": ["text", text]})
        dismiss_keyboard(driver)
        return
    except Exception:
        pass
    try:
        el.set_value(text)
        dismiss_keyboard(driver)
        return
    except Exception:
        pass
    el.send_keys(text)
    dismiss_keyboard(driver)


def wait_success_ui_cue_then_ocr_fast(driver) -> bool:
    """
    Nhanh hơn bản cũ:
    - Không OCR liên tục 12s nữa
    - Chỉ OCR tối đa OCR_MAX_CALLS lần, trong OCR_HARD_TIMEOUT giây
    - Luôn check popup fail ở giữa
    """
    end = now() + OCR_HARD_TIMEOUT
    ocr_calls = 0
    last_ocr = 0.0

    # cue: commit button xuất hiện => response-ready
    while now() < end:
        if click_fail_popup_ok(driver):
            return False
        if try_find_any_text(driver, BTN_COMMIT_EXACT, contains=False):
            break
        time.sleep(0.08)

    if not try_find_any_text(driver, BTN_COMMIT_EXACT, contains=False):
        return False

    # OCR limited
    while now() < end:
        if click_fail_popup_ok(driver):
            return False

        if ocr_calls < OCR_MAX_CALLS and (now() - last_ocr) >= OCR_MIN_GAP_SEC:
            last_ocr = now()
            ocr_calls += 1
            if ocr_you_saved_present(driver):
                return True

        # không sleep nhiều, nhưng đừng 0ms vì queue ADB
        time.sleep(0.08)

    return False


def commit_voucher(driver) -> None:
    btn = wait_any_text(driver, BTN_COMMIT_EXACT, timeout=T_WAIT_COMMIT_BTN, contains=False, poll=0.08)
    safe_tap_center(driver, btn)


def wait_promo_code(driver, timeout: float) -> bool:
    end = now() + timeout
    while now() < end:
        if promo_code_visible(driver):
            return True
        if click_fail_popup_ok(driver):
            return False
        time.sleep(0.08)
    return False


def apply_voucher_one_attempt(driver, code: str) -> bool:
    open_voucher_from_confirm(driver)

    inp = find_voucher_input(driver)
    set_text_fast(driver, inp, code)

    add_btn = wait_any_text(driver, BTN_ADD_EXACT, timeout=T_WAIT_ADD_BTN, contains=False, poll=0.08)
    safe_tap_center(driver, add_btn)

    # detect success fast (UI cue + OCR limited)
    success = wait_success_ui_cue_then_ocr_fast(driver)
    if not success:
        return False

    # commit then verify by post-condition
    commit_voucher(driver)
    return wait_promo_code(driver, timeout=T_WAIT_PROMO_CODE)


def spam_place_order_to_submit_fast(driver, timeout=T_SPAM_PLACE_TO_SUBMIT, poll=POLL_PLACE) -> bool:
    """
    Tăng tốc bằng cache tọa độ Place Order:
    - Tìm Place Order 1 lần để lấy (x,y)
    - Sau đó chủ yếu click tọa độ, chỉ check Submit mỗi vòng
    """
    end = now() + timeout

    # cache place order coordinate if possible
    place_xy = None
    place_el = try_find_any_text(driver, BTN_PLACE_ORDER_CONTAINS, contains=True)
    if place_el:
        r = place_el.rect
        place_xy = (int(r["x"] + r["width"] / 2), int(r["y"] + r["height"] / 2))

    while now() < end:
        # ưu tiên submit
        submit = try_find_any_text(driver, BTN_SUBMIT_EXACT, contains=False)
        if submit:
            safe_tap_center(driver, submit)
            return True

        click_fail_popup_ok(driver)

        # click place nhanh nhất có thể
        if place_xy:
            tap_xy(driver, place_xy[0], place_xy[1])
        else:
            place_el = try_find_any_text(driver, BTN_PLACE_ORDER_CONTAINS, contains=True)
            if place_el:
                r = place_el.rect
                place_xy = (int(r["x"] + r["width"] / 2), int(r["y"] + r["height"] / 2))
                tap_xy(driver, place_xy[0], place_xy[1])

        time.sleep(poll)

    return False


def build_driver():
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.automation_name = "UiAutomator2"
    options.udid = UDID
    options.app_package = "com.deliverynow"
    options.app_activity = "foody.vn.deliverynow.MainActivity"
    options.no_reset = True
    options.new_command_timeout = 150

    # OPPO fix + speed
    options.set_capability("appium:ignoreHiddenApiPolicyError", True)
    options.set_capability("appium:settings[waitForIdleTimeout]", 0)
    options.set_capability("appium:settings[actionAcknowledgmentTimeout]", 2000)
    options.set_capability("appium:settings[scrollAcknowledgmentTimeout]", 2000)
    options.set_capability("appium:skipLogcatCapture", True)
    options.set_capability("appium:settings[ignoreUnimportantViews]", False)

    driver = webdriver.Remote(APPIUM_URL, options=options)
    driver.implicitly_wait(0)
    try:
        driver.update_settings({"waitForIdleTimeout": 0, "waitForIdle": False})
    except Exception:
        pass
    return driver


def main():
    driver = build_driver()
    try:
        print("==> Session started")

        applied = False
        for attempt in range(1, MAX_ATTEMPTS + 1):
            print(f"\n==> Attempt {attempt}: apply voucher {VOUCHER_CODE}")
            applied = apply_voucher_one_attempt(driver, VOUCHER_CODE)
            print("   =>", "SUCCESS (Promotion Code visible)" if applied else "FAIL (popup OK / OCR miss / no promo)")

            if applied:
                break

        if not applied:
            print("==> FAILED: cannot apply voucher")
            return

        print("\n==> Promotion Code visible. Spam Place Order (100ms) -> Submit Now...")
        done = spam_place_order_to_submit_fast(driver)
        print("==> ORDER:", "SUBMITTED" if done else "FAILED (Submit not found)")

    finally:
        driver.quit()
        print("==> Done")


if __name__ == "__main__":
    main()