import os, sys, time, urllib.request, subprocess, json
import speech_recognition as sr
from seleniumbase import SB

# ==========================================
# 💡 从环境变量读取（GitHub Actions Secrets 注入）
# ==========================================
TARGET_URL = "https://g4f.gg/fzero"
MC_USERNAME = "fzero"
TG_TOKEN = os.getenv("TG_TOKEN", "")
TG_CHAT = os.getenv("TG_CHAT_ID", "")

def send_tg(msg, photo_path=None):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        data = json.dumps({"chat_id": TG_CHAT, "text": f"🤖 G4F 自动续期:\n{msg}"}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=10)
    except:
        pass

    if photo_path and os.path.exists(photo_path):
        try:
            import mimetypes
            boundary = '----g4fboundary'
            with open(photo_path, 'rb') as f:
                file_content = f.read()
            body = (
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{TG_CHAT}\r\n'
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="photo"; filename="{os.path.basename(photo_path)}"\r\n'
                f'Content-Type: image/png\r\n\r\n'
            ).encode('utf-8') + file_content + f'\r\n--{boundary}--\r\n'.encode('utf-8')

            url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
            req = urllib.request.Request(url, data=body,
                headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
            urllib.request.urlopen(req, timeout=15)
        except:
            pass

print(f"\n===== 🚀 开始执行极速续期 (WARP + Python 终极版) =====")

# 🌟 必须加回来：指定本地 WARP SOCKS5 代理
proxy_str = "socks5://127.0.0.1:40000"

def solve_recaptcha(sb):
    “””尝试过 reCAPTCHA，返回 True 表示通过，返回 'skip' 表示没验证码”””
    print(“🛡️ 检查是否存在 reCAPTCHA...”)

    # 先看页面有没有 reCAPTCHA iframe
    recaptcha_frame = 'iframe[title*=”reCAPTCHA”]'
    if not sb.is_element_visible(recaptcha_frame):
        print(“✅ 当前页面无 reCAPTCHA 验证，直接跳过。”)
        return “skip”

    sb.switch_to_frame(recaptcha_frame)

    print(“🖱️ 点击人机验证复选框...”)
    sb.wait_for_element('.recaptcha-checkbox-border', timeout=15)
    sb.click('.recaptcha-checkbox-border')
    sb.sleep(4)

    sb.switch_to_default_content()
    sb.switch_to_frame(recaptcha_frame)
    is_checked = sb.get_attribute('#recaptcha-anchor', 'aria-checked')

    if is_checked == 'true':
        print(“⏩ 运气爆表！IP 干净，验证码秒过。”)
        sb.switch_to_default_content()
        return True

    print(“⚠️ 触发挑战，正在尝试通过音频破解...”)
    sb.switch_to_default_content()
    sb.switch_to_frame('iframe[title*=”recaptcha challenge”]')

    if not sb.is_element_visible('#recaptcha-audio-button'):
        print(“❌ 当前 IP 无法加载音频，可能被 Google 临时屏蔽。”)
        sb.switch_to_default_content()
        return False

    sb.click('#recaptcha-audio-button')
    sb.sleep(3)

    if sb.is_text_visible(“Try again later”):
        print(“❌ IP 已黑，Google 拒绝下发音频。等待下次换 IP 重试。”)
        sb.switch_to_default_content()
        return False

    print(“📥 正在抓取音频数据流...”)
    audio_src = None
    if sb.is_element_visible('#audio-source'):
        audio_src = sb.get_attribute('#audio-source', 'src')
    elif sb.is_element_visible('.rc-audiochallenge-tdownload-link'):
        audio_src = sb.get_attribute('.rc-audiochallenge-tdownload-link', 'href')

    if not audio_src:
        print(“❌ 未能获取到音频链接。”)
        sb.switch_to_default_content()
        return False

    urllib.request.urlretrieve(audio_src, 'payload.mp3')
    subprocess.run(['ffmpeg', '-i', 'payload.mp3', 'payload.wav', '-y'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print(“🧠 AI 正在解析语音内容...”)
    r = sr.Recognizer()
    with sr.AudioFile('payload.wav') as source:
        audio_data = r.record(source)
    try:
        text = r.recognize_google(audio_data)
        print(f”✅ 识别成功: [{text}]”)
        sb.type('#audio-response', text)
        sb.click('#recaptcha-verify-button')
        sb.sleep(4)
        sb.switch_to_default_content()
        return True
    except sr.UnknownValueError:
        print(“❌ 引擎无法识别音频内容。”)
    except sr.RequestError as e:
        print(f”❌ 语音引擎请求错误: {e}”)

    sb.switch_to_default_content()
    return False


def do_renew(sb):
    “””填表 + 点击续期”””
    print(f”✍️ 填入服务器名: {MC_USERNAME}”)
    sb.type('input[type=”text”]', MC_USERNAME)

    os.makedirs(“screenshots”, exist_ok=True)
    sb.save_screenshot(“screenshots/1_filled.png”)

    print(“🚀 提交续期请求...”)
    sb.wait_for_element('#submit-button', timeout=10)
    sb.js_click('#submit-button')
    print(“🖱️ 成功执行模拟点击 Renew 按钮！”)

    print(“⏳ 等待服务器响应...”)
    sb.sleep(5)
    sb.save_screenshot(“screenshots/2_result.png”)

    if sb.is_text_visible(“The server has been renewed.”):
        print(“🎉 续期大成功！”)
        send_tg(f”✅ 服务器 [{MC_USERNAME}] 续期成功！(WARP IP)”, “screenshots/2_result.png”)
        return True
    else:
        print(“⚠️ 按钮已点，但未读取到成功横幅，请查阅截图确认。”)
        send_tg(f”⚠️ 续期已执行，请查阅截图确认状态。”, “screenshots/2_result.png”)
        return False


with SB(uc=True, proxy=proxy_str, headless=False) as sb:
    try:
        print(“🌐 正在通过 WARP SOCKS5 代理访问目标...”)
        sb.open(TARGET_URL)
        sb.sleep(3)

        result = solve_recaptcha(sb)
        if result is False:
            print(“❌ reCAPTCHA 破解失败，下次再试。”)
            sb.save_screenshot(“screenshots/error.png”)
            send_tg(“❌ reCAPTCHA 验证失败，无法续期。”, “screenshots/error.png”)
            sys.exit(1)

        # result 为 True（已过人机验证）或 “skip”（无验证码）都继续
        do_renew(sb)

    except Exception as e:
        print(f”❌ 发生致命错误: {e}”)
        os.makedirs(“screenshots”, exist_ok=True)
        sb.save_screenshot(“screenshots/error.png”)
        send_tg(f”❌ 自动续期崩溃: {e}”, “screenshots/error.png”)
