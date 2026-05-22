import os, sys, time, random, tempfile, subprocess, html
import requests
import speech_recognition as sr
from pydub import AudioSegment
from xvfbwrapper import Xvfb
from DrissionPage import ChromiumPage, ChromiumOptions

# ==========================================
# 配置区域
# ==========================================
TARGET_URL = "https://g4f.gg/fzero"
MC_USERNAME = "fzero"
TG_TOKEN = os.getenv("TG_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")

MAX_CAPTCHA_ATTEMPTS = 3


def log(msg, level="INFO"):
    prefix = {"INFO": "[INFO]", "WARN": "[WARN]", "ERROR": "[ERROR]"}.get(level, "[INFO]")
    print(f"{prefix} {msg}", flush=True)


class CaptchaBlocked(Exception):
    pass


# ==========================================
# Telegram 通知
# ==========================================
def send_tg(msg, photo_path=None):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": f"G4F 自动续期:\n{msg}"}, timeout=10)
    except Exception:
        pass

    if photo_path and os.path.exists(photo_path):
        try:
            url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
            with open(photo_path, "rb") as f:
                requests.post(url, data={"chat_id": TG_CHAT_ID, "caption": msg}, files={"photo": f}, timeout=15)
        except Exception:
            pass


# ==========================================
# WARP IP 轮换
# ==========================================
def restart_warp():
    log("正在重启 WARP 以更换 IP...")
    try:
        old_ip = requests.get("https://api.ipify.org", timeout=10).text
        log(f"当前 IP: {old_ip}")
    except Exception:
        old_ip = "unknown"
    try:
        subprocess.run(["sudo", "warp-cli", "--accept-tos", "disconnect"],
                       check=False, timeout=30, capture_output=True)
        time.sleep(3)
        try:
            subprocess.run(["sudo", "warp-cli", "--accept-tos", "registration", "delete"],
                           check=True, timeout=30, capture_output=True)
        except subprocess.CalledProcessError:
            log("删除注册失败（可能未注册）", "WARN")
        subprocess.run(["sudo", "warp-cli", "--accept-tos", "registration", "new"],
                       check=True, timeout=30, capture_output=True)
        time.sleep(3)
        subprocess.run(["sudo", "warp-cli", "--accept-tos", "connect"],
                       check=True, timeout=30, capture_output=True)
        time.sleep(10)
        new_ip = requests.get("https://api.ipify.org", timeout=10).text
        log(f"WARP 重连成功，新 IP: {new_ip}")
        return True
    except Exception as e:
        log(f"WARP 重连失败: {e}", "ERROR")
        return False


# ==========================================
# reCAPTCHA 辅助函数
# ==========================================
def find_recaptcha_frame(page, kind):
    """kind = 'anchor' 或 'bframe'"""
    for frame in page.get_frames():
        frame_url = frame.url or ""
        if "recaptcha" in frame_url and kind in frame_url:
            return frame
    return None


def is_recaptcha_solved(page):
    for frame in page.get_frames():
        try:
            token = frame.run_js("return document.querySelector(\"textarea[name='g-recaptcha-response']\")?.value")
            if token and len(token) > 30:
                return True
        except Exception:
            pass
    anchor = find_recaptcha_frame(page, "anchor")
    if anchor:
        try:
            checked = anchor.run_js("return document.querySelector('#recaptcha-anchor')?.getAttribute('aria-checked') === 'true'")
            if checked:
                return True
        except Exception:
            pass
    return False


def is_blocked(page):
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return False
    try:
        return bool(bframe.run_js("""
            const h = document.querySelector('.rc-doscaptcha-header-text');
            if (h && h.textContent.toLowerCase().includes('try again later')) return true;
            const e = document.querySelector('.rc-audiochallenge-error-message');
            if (e && e.offsetParent !== null) return true;
            return false;
        """))
    except Exception:
        return False


def click_recaptcha_checkbox(page):
    anchor = find_recaptcha_frame(page, "anchor")
    if not anchor:
        for _ in range(120):
            anchor = find_recaptcha_frame(page, "anchor")
            if anchor:
                break
            time.sleep(1)
    if not anchor:
        raise RuntimeError("未找到 reCAPTCHA anchor frame")
    checkbox = anchor.ele('#recaptcha-anchor', timeout=3)
    if not checkbox:
        raise RuntimeError("未找到 reCAPTCHA 复选框")
    page.actions.move_to(checkbox, duration=random.uniform(0.4, 1.0))
    time.sleep(random.uniform(0.2, 0.5))
    try:
        checkbox.click()
    except Exception:
        checkbox.click(by_js=True)
    time.sleep(3)
    if is_blocked(page):
        raise CaptchaBlocked("点击复选框后检测到 IP 被封锁")


def switch_to_audio(page):
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return False
    try:
        input_box = bframe.ele('#audio-response', timeout=1)
        if input_box and input_box.states.is_displayed:
            return True
    except Exception:
        pass
    for attempt in range(3):
        try:
            audio_btn = bframe.ele('#recaptcha-audio-button', timeout=3)
            if audio_btn:
                try:
                    audio_btn.click()
                except Exception:
                    audio_btn.click(by_js=True)
                time.sleep(3)
                if is_blocked(page):
                    raise CaptchaBlocked("点击音频按钮后检测到 IP 被封锁")
                input_box = bframe.ele('#audio-response', timeout=1)
                if input_box and input_box.states.is_displayed:
                    return True
        except CaptchaBlocked:
            raise
        except Exception:
            pass
        try:
            bframe.run_js("""
                const btn = document.querySelector('#recaptcha-audio-button');
                if (btn) btn.click();
            """)
            time.sleep(3)
            if is_blocked(page):
                raise CaptchaBlocked("JS点击音频按钮后检测到 IP 被封锁")
            input_box = bframe.ele('#audio-response', timeout=1)
            if input_box and input_box.states.is_displayed:
                return True
        except CaptchaBlocked:
            raise
        except Exception:
            pass
        time.sleep(2)
    return False


def is_audio_mode(page):
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return False
    try:
        input_box = bframe.ele('#audio-response', timeout=1)
        return bool(input_box and input_box.states.is_displayed)
    except Exception:
        return False


def get_audio_url(page):
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return None
    for _ in range(10):
        try:
            link = bframe.ele('.rc-audiochallenge-tdownload-link', timeout=1)
            if link:
                href = link.attr('href')
                if href and len(href) > 10:
                    return html.unescape(href)
            link = bframe.ele('.rc-audiochallenge-ndownload-link', timeout=1)
            if link:
                href = link.attr('href')
                if href and len(href) > 10:
                    return html.unescape(href)
            audio = bframe.ele('#audio-source', timeout=1)
            if audio:
                src = audio.attr('src')
                if src and len(src) > 10:
                    return html.unescape(src)
        except Exception:
            pass
        time.sleep(1)
    return None


def reload_challenge(page):
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return
    try:
        reload_btn = bframe.ele('#recaptcha-reload-button', timeout=2)
        if reload_btn:
            try:
                reload_btn.click()
            except Exception:
                reload_btn.click(by_js=True)
            time.sleep(3)
    except Exception:
        pass


def fill_and_verify(page, text):
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return False
    try:
        input_box = bframe.ele('#audio-response', timeout=2)
        if not input_box:
            return False
        input_box.click()
        input_box.clear()
        input_box.input(text)
    except Exception:
        return False
    time.sleep(random.uniform(0.5, 1.5))
    try:
        verify_btn = bframe.ele('#recaptcha-verify-button', timeout=2)
        if verify_btn:
            try:
                verify_btn.click()
            except Exception:
                verify_btn.click(by_js=True)
    except Exception:
        pass
    return True


def download_audio(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.google.com/",
    }
    urls = [url]
    if "recaptcha.net" in url:
        urls.append(url.replace("recaptcha.net", "www.google.com"))
    elif "google.com" in url:
        urls.append(url.replace("www.google.com", "recaptcha.net"))
    for audio_url in urls:
        try:
            r = requests.get(audio_url, headers=headers, timeout=30)
            r.raise_for_status()
            if len(r.content) < 1000:
                continue
            path = tempfile.mktemp(suffix=".mp3")
            with open(path, "wb") as f:
                f.write(r.content)
            return path
        except Exception:
            pass
    return None


def recognize_audio(mp3_path):
    try:
        wav_path = mp3_path.replace(".mp3", ".wav")
        AudioSegment.from_mp3(mp3_path).export(wav_path, format="wav")
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as src:
            audio_data = recognizer.record(src)
            text = recognizer.recognize_google(audio_data)
        try:
            os.remove(wav_path)
        except Exception:
            pass
        return text
    except Exception:
        return None


# ==========================================
# reCAPTCHA 主流程
# ==========================================
def solve_recaptcha(page):
    for _ in range(15):
        if find_recaptcha_frame(page, "anchor"):
            break
        time.sleep(1)
    else:
        log("未检测到 reCAPTCHA，跳过验证")
        return "skip"

    dl_fails = 0
    for i in range(MAX_CAPTCHA_ATTEMPTS):
        if is_recaptcha_solved(page):
            log("reCAPTCHA 已验证通过")
            return True
        if is_blocked(page):
            raise CaptchaBlocked("IP 被 Google reCAPTCHA 封锁")

        if i == 0:
            log("点击 reCAPTCHA 复选框...")
            click_recaptcha_checkbox(page)
            time.sleep(2)
            if is_recaptcha_solved(page):
                log("秒过！IP 干净")
                return True

        if not is_audio_mode(page):
            log("切换到音频验证模式...")
            if not switch_to_audio(page):
                time.sleep(3)
                if not switch_to_audio(page):
                    click_recaptcha_checkbox(page)
                    time.sleep(3)
                    continue
            time.sleep(random.uniform(2, 4))

        if is_blocked(page):
            raise CaptchaBlocked("音频模式检测到 IP 被封锁")

        audio_url = get_audio_url(page)
        if not audio_url:
            log("未获取到音频链接，重载挑战...", "WARN")
            reload_challenge(page)
            continue

        mp3 = download_audio(audio_url)
        if not mp3:
            dl_fails += 1
            if dl_fails >= 3:
                raise RuntimeError("音频连续下载失败")
            reload_challenge(page)
            time.sleep(random.uniform(3, 6))
            continue
        dl_fails = 0

        text = recognize_audio(mp3)
        try:
            os.remove(mp3)
        except Exception:
            pass
        if not text:
            log("语音识别失败，重载挑战", "WARN")
            reload_challenge(page)
            time.sleep(3)
            continue

        log(f"识别结果: [{text}]")
        fill_and_verify(page, text)
        time.sleep(5)
        if is_recaptcha_solved(page):
            log("验证码通过")
            return True
        reload_challenge(page)
        time.sleep(random.uniform(2, 4))

    raise RuntimeError("验证码达到最大尝试次数")


# ==========================================
# g4f.gg 续期流程
# ==========================================
def do_renew(page):
    log(f"填入服务器名: {MC_USERNAME}")
    input_ele = page.ele('tag:input@@type=text', timeout=5)
    if not input_ele:
        raise RuntimeError("未找到文本输入框")
    input_ele.click()
    input_ele.clear()
    input_ele.input(MC_USERNAME)

    os.makedirs("screenshots", exist_ok=True)
    page.get_screenshot(path="screenshots/1_filled.png")

    log("提交续期请求...")
    submit_btn = page.ele('#submit-button', timeout=10)
    if not submit_btn:
        submit_btn = page.ele('xpath://button[contains(text(), "Renew")]', timeout=3)
    if not submit_btn:
        submit_btn = page.ele('xpath://button[contains(text(), "Submit")]', timeout=3)
    if not submit_btn:
        raise RuntimeError("未找到提交按钮")

    try:
        submit_btn.click()
    except Exception:
        submit_btn.click(by_js=True)
    log("成功点击 Renew 按钮")

    time.sleep(5)
    page.get_screenshot(path="screenshots/2_result.png")

    page_text = (page.html or "").lower()
    if "the server has been renewed" in page_text or "successfully" in page_text or "renewed" in page_text:
        log("续期大成功！")
        send_tg(f"服务器 [{MC_USERNAME}] 续期成功！", "screenshots/2_result.png")
        return True
    else:
        log("未读取到成功横幅，请查阅截图确认", "WARN")
        send_tg(f"续期已执行，请查阅截图确认状态。", "screenshots/2_result.png")
        return False


# ==========================================
# 主入口
# ==========================================
def main():
    log("===== G4F 自动续期 (DrissionPage 增强版) =====")

    vdisplay = Xvfb(width=1280, height=720, colordepth=24)
    vdisplay.start()

    try:
        co = ChromiumOptions()
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-dev-shm-usage')
        co.set_argument('--disable-gpu')
        co.set_argument('--window-size=1280,720')
        co.set_argument('--log-level=3')
        co.set_argument('--silent')
        co.auto_port()
        co.headless(False)
        page = ChromiumPage(co)

        # 反指纹注入
        page.add_init_js("""
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) return 'Intel Inc.';
                if (parameter === 37446) return 'Intel(R) UHD Graphics 630';
                return getParameter.apply(this, [parameter]);
            };
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
        """)

        log(f"访问: {TARGET_URL}")
        page.get(TARGET_URL, retry=3)
        time.sleep(random.uniform(5, 8))

        # 人味行为：滚动 + 鼠标移动
        for _ in range(3):
            scroll_y = random.randint(200, 600)
            page.scroll.down(scroll_y)
            time.sleep(random.uniform(0.5, 1.5))
            page.actions.move(random.randint(100, 800), random.randint(100, 500))
            time.sleep(random.uniform(0.5, 1.0))
        time.sleep(random.uniform(1.0, 2.0))

        # reCAPTCHA
        result = solve_recaptcha(page)
        if result is False:
            log("reCAPTCHA 破解失败", "ERROR")
            page.get_screenshot(path="screenshots/error.png")
            send_tg("reCAPTCHA 验证失败，无法续期。", "screenshots/error.png")
            sys.exit(1)

        do_renew(page)

    except CaptchaBlocked:
        log("IP 被封锁，尝试更换 IP...", "WARN")
        page.get_screenshot(path="screenshots/error.png")
        send_tg("IP 被 reCAPTCHA 封锁，需要换 IP 重试。", "screenshots/error.png")
        restart_warp()
        sys.exit(1)
    except Exception as e:
        log(f"致命错误: {e}", "ERROR")
        os.makedirs("screenshots", exist_ok=True)
        try:
            page.get_screenshot(path="screenshots/error.png")
        except Exception:
            pass
        send_tg(f"自动续期崩溃: {e}", "screenshots/error.png")
        sys.exit(1)
    finally:
        try:
            page.quit()
        except Exception:
            pass
        vdisplay.stop()


if __name__ == "__main__":
    main()
