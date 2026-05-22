import os, sys, time, random, tempfile, subprocess
import requests
from xvfbwrapper import Xvfb
from DrissionPage import ChromiumPage, ChromiumOptions

# ==========================================
# 配置区域
# ==========================================
TARGET_URL = "https://g4f.gg/fzero"
MC_USERNAME = "fzero"
TG_TOKEN = os.getenv("TG_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")


def log(msg, level="INFO"):
    prefix = {"INFO": "[INFO]", "WARN": "[WARN]", "ERROR": "[ERROR]"}.get(level, "[INFO]")
    print(f"{prefix} {msg}", flush=True)


# ==========================================
# Telegram 通知
# ==========================================
def build_notification(success, failure_reason=""):
    lines = []
    if success:
        lines = [
            "✅ 续期成功",
            "",
            f"服务器：{MC_USERNAME}",
            f"URL：{TARGET_URL}",
        ]
    else:
        lines = [
            "❌ 续期失败",
            "",
            f"服务器：{MC_USERNAME}",
            f"URL：{TARGET_URL}",
        ]
        if failure_reason:
            lines.append(f"失败原因：{failure_reason}")
    lines.append("")
    lines.append("G4F Auto Renew")
    return "\n".join(lines)


def send_tg(caption, photo_path=None):
    if not TG_TOKEN or not TG_CHAT_ID:
        log("TG_TOKEN 或 TG_CHAT_ID 未配置，跳过 Telegram 通知", "WARN")
        return
    if photo_path and os.path.exists(photo_path):
        try:
            url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
            with open(photo_path, "rb") as f:
                r = requests.post(url, data={"chat_id": TG_CHAT_ID, "caption": caption},
                                  files={"photo": f}, timeout=15)
            if not r.ok:
                log(f"Telegram 图片通知失败: {r.text}", "WARN")
            else:
                log("Telegram 通知已发送")
        except Exception as e:
            log(f"Telegram 通知异常: {e}", "ERROR")
    else:
        try:
            url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
            r = requests.post(url, json={"chat_id": TG_CHAT_ID, "text": caption}, timeout=10)
            if r.ok:
                log("Telegram 通知已发送")
        except Exception as e:
            log(f"Telegram 通知异常: {e}", "ERROR")


# ==========================================
# g4f.gg 续期流程
# ==========================================
def do_renew(page):
    # 1. 填表
    log(f"填入玩家名: {MC_USERNAME}")
    input_ele = page.ele('tag:input@@name=voter_name', timeout=5)
    if not input_ele:
        input_ele = page.ele('.name-input', timeout=5)
    if not input_ele:
        raise RuntimeError("未找到输入框")

    input_ele.click()
    input_ele.clear()
    input_ele.input(MC_USERNAME)

    os.makedirs("screenshots", exist_ok=True)
    page.get_screenshot(path="screenshots/1_filled.png")

    # 2. 点击按钮
    log("提交续期请求...")
    submit_btn = page.ele('.vote-btn', timeout=10)
    if not submit_btn:
        submit_btn = page.ele('tag:button@@text()=+ ADD 3 HOURS', timeout=3)
    if not submit_btn:
        raise RuntimeError("未找到提交按钮")

    try:
        submit_btn.click()
    except Exception:
        submit_btn.click(by_js=True)
    log("成功点击续期按钮")

    # 3. 等响应
    time.sleep(5)
    page_text = (page.html or "").lower()
    page.get_screenshot(path="screenshots/2_result.png")

    # 4. 检查结果
    # 成功标志："3 hours added" / "thanks for supporting"
    if "3 hours added" in page_text or "thanks for supporting" in page_text:
        log("续期成功！+3 小时")
        caption = build_notification(success=True)
        send_tg(caption, "screenshots/2_result.png")
        return True

    # 冷却提示：You extended this server recently（之前的投票已续期，仍然算成功）
    if "you extended this server recently" in page_text:
        log("冷却中，服务器已在之前被续期，无需重复操作")
        caption = build_notification(success=True)
        send_tg(caption, "screenshots/2_result.png")
        return True

    # 失败提示
    if ".flash-error" in page_text or "something went wrong" in page_text:
        flash_err = page.ele('.flash-error', timeout=1)
        err_text = flash_err.text.strip() if flash_err else "未知错误"
        log(f"失败提示: {err_text}", "WARN")
        caption = build_notification(success=False, failure_reason=err_text)
        send_tg(caption, "screenshots/2_result.png")
        return False

    log("未读取到明确成功/失败标志，请查阅截图确认", "WARN")
    caption = build_notification(success=False, failure_reason="未检测到成功标志")
    send_tg(caption, "screenshots/2_result.png")
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
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
        """)

        log(f"访问: {TARGET_URL}")
        page.get(TARGET_URL, retry=3)
        time.sleep(random.uniform(5, 8))

        # 模拟人类行为：滚动 + 鼠标移动
        for _ in range(3):
            scroll_y = random.randint(200, 600)
            page.scroll.down(scroll_y)
            time.sleep(random.uniform(0.5, 1.5))
            page.actions.move(random.randint(100, 800), random.randint(100, 500))
            time.sleep(random.uniform(0.5, 1.0))
        time.sleep(random.uniform(1.0, 2.0))

        do_renew(page)

    except Exception as e:
        log(f"致命错误: {e}", "ERROR")
        os.makedirs("screenshots", exist_ok=True)
        screenshot = None
        try:
            page.get_screenshot(path="screenshots/error.png")
            screenshot = "screenshots/error.png"
        except Exception:
            pass
        caption = build_notification(success=False, failure_reason=str(e)[:200])
        send_tg(caption, screenshot)
        sys.exit(1)
    finally:
        try:
            page.quit()
        except Exception:
            pass
        vdisplay.stop()


if __name__ == "__main__":
    main()
