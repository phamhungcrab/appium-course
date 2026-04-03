import time
from io import BytesIO

from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy

from PIL import Image
import pytesseract

from selenium.common.exceptions import TimeoutException


# ================== CONFIG ==================
TESSERACT_EXE = r"C:\Users\HUNG PHAM\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
pytesseract.pytesseract.tesseract_cmd = TESSERACT_EXE

UDID = "emulator-5554"
#VOUCHER_CODE = "44SPFMOI35"
VOUCHER_CODE = "44SPFMOI50"
#VOUCHER_CODE = "44SPFMOI60"
# VOUCHER_CODE = "44SPFMOI100"
# VOUCHER_CODE = "BAUFSSSPF14"

MAX_ATTEMPTS = 50
NETWORK_TIMEOUT_SEC = 8.0
POLL_SEC = 0.08

DEBUG = True
DEBUG_SAVE_ROI = False
# ===========================================


# ====== XPATHS ======
APPLY_XPATHS = [
    r'//android.widget.FrameLayout[@resource-id="android:id/content"]/android.widget.FrameLayout/android.widget.FrameLayout/android.view.ViewGroup/android.view.ViewGroup/android.view.ViewGroup[1]/android.view.ViewGroup[2]/android.view.ViewGroup[2]',
    r'//android.widget.FrameLayout[@resource-id="android:id/content"]/android.widget.FrameLayout/android.widget.FrameLayout/android.view.ViewGroup/android.view.ViewGroup/android.view.ViewGroup[2]/android.view.ViewGroup[2]',
]

POPUP_OK_XPATH = r'//android.widget.FrameLayout[@resource-id="android:id/content"]/android.widget.FrameLayout/android.widget.FrameLayout/android.view.ViewGroup/android.view.ViewGroup[2]/android.view.ViewGroup[2]/android.view.ViewGroup[2]/android.view.ViewGroup'
# ================================


def log(msg: str):
    if DEBUG:
        print(msg, flush=True)


def screenshot_rgb(driver) -> Image.Image:
    png = driver.get_screenshot_as_png()
    return Image.open(BytesIO(png)).convert("RGB")


def safe_click(driver, el):
    try:
        el.click()
        return
    except Exception:
        pass
    try:
        driver.execute_script("mobile: clickGesture", {"elementId": el.id})
        return
    except Exception:
        pass
    r = el.rect
    x = int(r["x"] + r["width"] / 2)
    y = int(r["y"] + r["height"] / 2)
    driver.execute_script("mobile: clickGesture", {"x": x, "y": y})


def tap(driver, x, y):
    driver.execute_script("mobile: clickGesture", {"x": x, "y": y})


def find_one(driver, by, value, timeout=2.5):
    end = time.time() + timeout
    while time.time() < end:
        els = driver.find_elements(by, value)
        if els:
            return els[0]
        time.sleep(0.05)
    raise TimeoutException(f"Not found in {timeout}s: {by}={value}")


def try_find(driver, by, value, timeout=1.0):
    end = time.time() < timeout
    end = time.time() + timeout
    while time.time() < end:
        els = driver.find_elements(by, value)
        if els:
            return els[0]
        time.sleep(0.05)
    return None


def set_text_fast(driver, el, text: str):
    """Nhập text — dùng hide_keyboard() vì press_keycode(4) BACK đóng cả trang."""
    try:
        el.set_value(text)
    except Exception:
        safe_click(driver, el)
        el.send_keys(text)
    try:
        driver.hide_keyboard()
    except Exception:
        pass


# ---------- OCR ----------

def crop_bottom_half(img_rgb: Image.Image) -> Image.Image:
    w, h = img_rgb.size
    y1 = int(h * 0.5)
    crop = img_rgb.crop((0, y1, w, h)).convert("L")
    crop = crop.point(lambda p: 255 if p > 190 else p)
    return crop


def ocr_has_you_saved(driver) -> bool:
    img = screenshot_rgb(driver)
    roi = crop_bottom_half(img)
    if DEBUG_SAVE_ROI:
        roi.save("debug_you_saved_roi.png")
    text = pytesseract.image_to_string(roi, config="--psm 6 -l eng").strip().lower()
    if DEBUG:
        log(f"[OCR] '{text[:90]}'")
    return "you saved" in text


# ---------- Popup OK (fail) ----------

def try_click_popup_ok(driver) -> bool:
    ok_popup = try_find(driver, AppiumBy.XPATH, POPUP_OK_XPATH, timeout=0.25)
    if ok_popup:
        safe_click(driver, ok_popup)
        return True
    ok_sys = try_find(driver, AppiumBy.ID, "android:id/button1", timeout=0.15)
    if ok_sys:
        safe_click(driver, ok_sys)
        return True
    return False


# ---------- Apply click ----------

def click_apply(driver):
    for xp in APPLY_XPATHS:
        el = try_find(driver, AppiumBy.XPATH, xp, timeout=0.6)
        if el:
            safe_click(driver, el)
            return
    el = try_find(driver, AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("Apply")', timeout=0.6)
    if el:
        safe_click(driver, el)
        return
    raise TimeoutException("Cannot find/click Apply button")


# ---------- Submit Now (OCR + tap) ----------

def click_submit_now(driver, timeout=10.0) -> bool:
    """
    Nút Submit Now là overlay, Appium Inspector không thấy.
    OCR quét vùng y=1512..1611 rồi tap giữa.
    """
    end = time.time() + timeout
    while time.time() < end:
        img = screenshot_rgb(driver)
        roi = img.crop((0, 1512, img.size[0], 1611)).convert("L")
        roi = roi.point(lambda p: 255 if p > 190 else p)
        text = pytesseract.image_to_string(roi, config="--psm 6 -l eng").strip().lower()
        log(f"[OCR Submit] '{text[:60]}'")
        if "submit" in text:
            cx = int(img.size[0] * 0.75)  # 3/4 phải — Submit Now nằm bên phải, Edit bên trái
            cy = (1512 + 1611) // 2
            tap(driver, cx, cy)
            log(f"==> Clicked Submit Now (tap {cx}, {cy})")
            return True
        time.sleep(0.3)
    return False


# ================== MAIN FLOW ==================

def apply_voucher_flow(driver) -> bool:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        log(f"\n==> Attempt {attempt} code={VOUCHER_CODE}")

        # input
        inp = find_one(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                       'new UiSelector().className("android.widget.EditText").instance(0)',
                       timeout=3.0)
        set_text_fast(driver, inp, VOUCHER_CODE)

        # click apply
        click_apply(driver)
        log("==> Clicked Apply")

        # Poll: chờ OK text xuất hiện (nhanh) -> rồi mới OCR xác nhận 1 lần
        end = time.time() + NETWORK_TIMEOUT_SEC
        success = False
        while time.time() < end:
            # Nhanh: check popup fail trước (timeout rất ngắn)
            if try_click_popup_ok(driver):
                log("==> FAIL: error popup -> retry")
                break

            # Nhanh: check có nút text "OK" xuất hiện chưa (không OCR)
            ok_btn = try_find(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                              'new UiSelector().text("OK")', timeout=0.15)
            if ok_btn:
                # Có nút OK -> OCR 1 lần duy nhất để xác nhận success
                if ocr_has_you_saved(driver):
                    log("==> SUCCESS: 'You saved' + OK found -> CLICK")
                    safe_click(driver, ok_btn)
                    log("==> Clicked OK")
                    time.sleep(2)  # chờ UI chuyển trang
                    success = True
                    break
                # Nếu OCR không thấy "You saved" = có thể popup lỗi
                # -> vòng tiếp sẽ catch bằng try_click_popup_ok

            time.sleep(POLL_SEC)

        if success:
            return True

    return False


def main():
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.automation_name = "UiAutomator2"
    options.udid = UDID
    options.app_package = "com.deliverynow"
    options.app_activity = "foody.vn.deliverynow.MainActivity"
    options.no_reset = True
    options.new_command_timeout = 120
    options.set_capability("appium:adbExecTimeout", 120000)

    driver = webdriver.Remote("http://127.0.0.1:4723", options=options)

    try:
        print("==> Session started")
        try:
            driver.update_settings({"waitForIdle": False, "waitForIdleTimeout": 0})
        except Exception:
            pass

        # Step 1: Apply voucher
        ok = apply_voucher_flow(driver)
        if not ok:
            print("==> FAILED: cannot apply voucher")
            return

        # Step 2: Place Order -> Correct/Submit Now
        #   Spam click Place Order, sau đó check Correct (text) hoặc Submit Now (OCR)
        print("==> Voucher OK. Click Place Order...")
        done = False
        end_po = time.time() + 40.0
        while time.time() < end_po and not done:
            # Click Place Order liên tục
            place = try_find(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                             'new UiSelector().textContains("Place Order")', timeout=0.3)
            if place:
                safe_click(driver, place)
                log("==> Clicked Place Order")

            # Check Correct (nhanh, tìm text)
            correct = try_find(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                               'new UiSelector().text("Correct")', timeout=0.15)
            if correct:
                safe_click(driver, correct)
                log("==> Clicked Correct")
                time.sleep(0.5)
                continue  # sau Correct có thể cần Submit Now

            # Check Submit Now (OCR 1 lần)
            img = screenshot_rgb(driver)
            roi = img.crop((0, 1512, img.size[0], 1611)).convert("L")
            roi = roi.point(lambda p: 255 if p > 190 else p)
            text = pytesseract.image_to_string(roi, config="--psm 6 -l eng").strip().lower()
            log(f"[OCR Submit] '{text[:60]}'")
            if "submit" in text:
                cx = int(img.size[0] * 0.75)
                cy = (1512 + 1611) // 2
                tap(driver, cx, cy)
                print(f"==> Clicked Submit Now (tap {cx}, {cy})")
                print("==> ORDER PLACED SUCCESSFULLY!")
                done = True
                break

        if not done:
            print("==> FAILED: cannot complete Place Order -> Submit Now")

    finally:
        driver.quit()
        print("==> Done")


if __name__ == "__main__":
    main()