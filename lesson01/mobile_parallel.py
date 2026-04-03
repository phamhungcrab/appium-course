import time
from dataclasses import dataclass
from io import BytesIO
from multiprocessing import Process, freeze_support
from typing import List

from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy

from PIL import Image
import pytesseract

from selenium.common.exceptions import TimeoutException


# ================== SIMPLE CONFIG ==================
TESSERACT_EXE = r"C:\Users\HUNG PHAM\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
pytesseract.pytesseract.tesseract_cmd = TESSERACT_EXE

APPIUM_URL = "http://127.0.0.1:4723"

# Giữ nguyên app config của file cũ.
# Nếu bản 1 máy của bạn đang chạy với package/activity khác, sửa 2 dòng này theo file đang chạy thật của bạn.
APP_PACKAGE = "com.deliverynow"
APP_ACTIVITY = "foody.vn.deliverynow.MainActivity"

# Chỉ cần điền UDID và voucher ở đây.
# Mỗi item = 1 luồng chạy song song.
DEVICES = [
    {"udid": "emulator-5554", "voucher": "44SPFMOI50"},
    {"udid": "emulator-5556", "voucher": "44SPFMOI60"},
    # {"udid": "ec33abab", "voucher": "BAUFSSSPF14"},
]

# systemPort sẽ tự sinh: 8201, 8202, 8203...
SYSTEM_PORT_BASE = 8200

MAX_ATTEMPTS = 50
NETWORK_TIMEOUT_SEC = 8.0
POLL_SEC = 0.08
PLACE_ORDER_TIMEOUT_SEC = 40.0

DEBUG = True
DEBUG_SAVE_ROI = False
# ================================================


# ====== XPATHS ======
APPLY_XPATHS = [
    r'//android.widget.FrameLayout[@resource-id="android:id/content"]/android.widget.FrameLayout/android.widget.FrameLayout/android.view.ViewGroup/android.view.ViewGroup/android.view.ViewGroup[1]/android.view.ViewGroup[2]/android.view.ViewGroup[2]',
    r'//android.widget.FrameLayout[@resource-id="android:id/content"]/android.widget.FrameLayout/android.widget.FrameLayout/android.view.ViewGroup/android.view.ViewGroup/android.view.ViewGroup[2]/android.view.ViewGroup[2]',
]

POPUP_OK_XPATH = r'//android.widget.FrameLayout[@resource-id="android:id/content"]/android.widget.FrameLayout/android.widget.FrameLayout/android.view.ViewGroup/android.view.ViewGroup[2]/android.view.ViewGroup[2]/android.view.ViewGroup[2]/android.view.ViewGroup'
# ====================


@dataclass
class DeviceJob:
    udid: str
    voucher: str
    system_port: int


def log(job: DeviceJob, msg: str):
    if DEBUG:
        print(f"[{job.udid}] {msg}", flush=True)


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
    end = time.time() + timeout
    while time.time() < end:
        els = driver.find_elements(by, value)
        if els:
            return els[0]
        time.sleep(0.05)
    return None


def set_text_fast(driver, el, text: str):
    """Nhập text nhanh, không dùng BACK để tránh đóng popup/trang."""
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


def ocr_has_you_saved(driver, job: DeviceJob) -> bool:
    img = screenshot_rgb(driver)
    roi = crop_bottom_half(img)

    if DEBUG_SAVE_ROI:
        roi.save(f"debug_you_saved_roi_{job.udid}.png")

    text = pytesseract.image_to_string(roi, config="--psm 6 -l eng").strip().lower()
    log(job, f"[OCR] '{text[:90]}'")
    return "you saved" in text


def click_submit_now(driver, job: DeviceJob, timeout=10.0) -> bool:
    """
    Nút Submit Now là overlay, Appium Inspector có thể không thấy.
    OCR quét vùng y=1512..1611 rồi tap giữa.
    """
    end = time.time() + timeout
    while time.time() < end:
        img = screenshot_rgb(driver)
        roi = img.crop((0, 1512, img.size[0], 1611)).convert("L")
        roi = roi.point(lambda p: 255 if p > 190 else p)
        text = pytesseract.image_to_string(roi, config="--psm 6 -l eng").strip().lower()
        log(job, f"[OCR Submit] '{text[:60]}'")

        if "submit" in text:
            cx = int(img.size[0] * 0.75)  # Submit Now bên phải, Edit bên trái
            cy = (1512 + 1611) // 2
            tap(driver, cx, cy)
            log(job, f"==> Clicked Submit Now (tap {cx}, {cy})")
            return True

        time.sleep(0.3)

    return False


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


# ================== MAIN FLOW ==================
def apply_voucher_flow(driver, job: DeviceJob) -> bool:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        log(job, f"\n==> Attempt {attempt} code={job.voucher}")

        inp = find_one(
            driver,
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().className("android.widget.EditText").instance(0)',
            timeout=3.0,
        )
        set_text_fast(driver, inp, job.voucher)

        click_apply(driver)
        log(job, "==> Clicked Apply")

        end = time.time() + NETWORK_TIMEOUT_SEC
        success = False

        while time.time() < end:
            if try_click_popup_ok(driver):
                log(job, "==> FAIL: error popup -> retry")
                break

            ok_btn = try_find(
                driver,
                AppiumBy.ANDROID_UIAUTOMATOR,
                'new UiSelector().text("OK")',
                timeout=0.15
            )
            if ok_btn:
                if ocr_has_you_saved(driver, job):
                    log(job, "==> SUCCESS: 'You saved' + OK found -> CLICK")
                    safe_click(driver, ok_btn)
                    log(job, "==> Clicked OK")
                    time.sleep(2)
                    success = True
                    break

            time.sleep(POLL_SEC)

        if success:
            return True

    return False


def place_order_flow(driver, job: DeviceJob) -> bool:
    print(f"[{job.udid}] ==> Voucher OK. Click Place Order...", flush=True)

    done = False
    end_po = time.time() + PLACE_ORDER_TIMEOUT_SEC

    while time.time() < end_po and not done:
        place = try_find(
            driver,
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().textContains("Place Order")',
            timeout=0.3
        )
        if place:
            safe_click(driver, place)
            log(job, "==> Clicked Place Order")

        correct = try_find(
            driver,
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().text("Correct")',
            timeout=0.15
        )
        if correct:
            safe_click(driver, correct)
            log(job, "==> Clicked Correct")
            time.sleep(0.5)
            continue

        if click_submit_now(driver, job, timeout=0.5):
            print(f"[{job.udid}] ==> ORDER PLACED SUCCESSFULLY!", flush=True)
            done = True
            break

    return done


def build_driver(job: DeviceJob):
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.automation_name = "UiAutomator2"
    options.udid = job.udid
    options.app_package = APP_PACKAGE
    options.app_activity = APP_ACTIVITY
    options.no_reset = True
    options.new_command_timeout = 120

    # Quan trọng để chạy song song Android
    options.set_capability("appium:systemPort", job.system_port)
    options.set_capability("appium:adbExecTimeout", 120000)

    driver = webdriver.Remote(APPIUM_URL, options=options)

    try:
        driver.update_settings({"waitForIdle": False, "waitForIdleTimeout": 0})
    except Exception:
        pass

    return driver


def build_jobs() -> List[DeviceJob]:
    jobs: List[DeviceJob] = []

    for idx, item in enumerate(DEVICES, start=1):
        udid = item["udid"].strip()
        voucher = item["voucher"].strip()

        if not udid:
            raise ValueError(f"DEVICES[{idx}] thiếu udid")
        if not voucher:
            raise ValueError(f"DEVICES[{idx}] thiếu voucher")

        jobs.append(
            DeviceJob(
                udid=udid,
                voucher=voucher,
                system_port=SYSTEM_PORT_BASE + idx,
            )
        )

    # Check trùng UDID
    udids = [job.udid for job in jobs]
    if len(udids) != len(set(udids)):
        raise ValueError("UDID bị trùng trong DEVICES")

    return jobs


def run_one_device(job: DeviceJob):
    driver = None
    try:
        log(job, f"Starting session | systemPort={job.system_port}")
        driver = build_driver(job)
        print(f"[{job.udid}] ==> Session started", flush=True)

        ok = apply_voucher_flow(driver, job)
        if not ok:
            print(f"[{job.udid}] ==> FAILED: cannot apply voucher", flush=True)
            return

        done = place_order_flow(driver, job)
        if not done:
            print(f"[{job.udid}] ==> FAILED: cannot complete Place Order -> Submit Now", flush=True)

    except Exception as e:
        print(f"[{job.udid}] ==> ERROR: {e}", flush=True)
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        print(f"[{job.udid}] ==> Done", flush=True)


def main():
    jobs = build_jobs()

    print("=== ACTIVE JOBS ===", flush=True)
    for job in jobs:
        print(
            f"- udid={job.udid} | voucher={job.voucher} | systemPort={job.system_port}",
            flush=True
        )

    if len(jobs) == 1:
        run_one_device(jobs[0])
        return

    processes = []
    for job in jobs:
        p = Process(target=run_one_device, args=(job,))
        p.start()
        processes.append(p)

    for p in processes:
        p.join()

    print("=== ALL JOBS FINISHED ===", flush=True)


if __name__ == "__main__":
    freeze_support()  # cần cho Windows multiprocessing
    main()