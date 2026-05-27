import os, sys, time, random, re, subprocess
from datetime import datetime
import requests
from xvfbwrapper import Xvfb
from seleniumbase import SB

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
def parse_expiry_from_page(page_source):
    m = re.search(r'expires\s+(\w{3}\s+\d{1,2},\s+\d{4}\s+at\s+\d{2}:\d{2}\s+UTC)', page_source)
    if m:
        try:
            return datetime.strptime(m.group(1), "%b %d, %Y at %H:%M UTC")
        except ValueError:
            pass
    return None


def build_notification(success, failure_reason="", expiry=None):
    lines = []
    if success:
        lines = [
            "✅ 续期成功",
            "",
            f"服务器：{MC_USERNAME}",
            f"URL：{TARGET_URL}",
        ]
        if expiry:
            now = datetime.utcnow()
            remaining_days = (expiry - now).total_seconds() / 86400.0
            lines.append(f"到期时间：{expiry.strftime('%Y-%m-%d %H:%M')} UTC")
            lines.append(f"剩余天数：{remaining_days:.1f} 天")
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
# WARP IP 切换
# ==========================================
def rotate_warp_ip():
    """断开并重连 WARP 获取新出口 IP"""
    log("正在切换 WARP IP...")
    try:
        subprocess.run(["sudo", "warp-cli", "disconnect"], capture_output=True, timeout=15)
        time.sleep(2)
        subprocess.run(["sudo", "warp-cli", "connect"], capture_output=True, timeout=30)
        time.sleep(5)
        result = subprocess.run(
            ["curl", "-s", "--max-time", "10", "https://api.ipify.org"],
            capture_output=True, text=True, timeout=15
        )
        new_ip = result.stdout.strip() if result.returncode == 0 else "未知"
        log(f"WARP 新 IP: {new_ip}")
        return True
    except Exception as e:
        log(f"WARP IP 切换失败: {e}", "WARN")
        return False


# ==========================================
# CF Challenge 等待（JS Challenge 拦截页）
# ==========================================
def wait_for_page_ready(sb, timeout=30):
    """等待 CF JS Challenge 通过，页面加载完成"""
    log("等待页面加载完成...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            ready = sb.execute_script(
                "return document.readyState === 'complete'"
                " && document.querySelector('.vote-card') !== null;"
            )
            if ready:
                log("页面加载完成，CF Challenge 已通过")
                return True

            page_source = sb.get_page_source() or ""
            if "challenge-platform" in page_source:
                log("仍在 CF Challenge 页面，等待...")
            else:
                log("页面加载中...")
        except Exception:
            log("页面状态检测异常，等待重试...")
        time.sleep(2)
    log("等待页面加载超时", "WARN")
    return False


# ==========================================
# Turnstile 弹窗处理
# ==========================================
def is_modal_open(sb):
    display = sb.execute_script(
        "var m = document.getElementById('captcha-modal');"
        "return m ? m.style.display : 'none';"
    )
    return display == "flex"


def has_turnstile_token(sb, token_input_id="vote-turnstile-token"):
    token = sb.execute_script(
        f"var i = document.getElementById('{token_input_id}');"
        "return i ? i.value : '';"
    )
    return bool(token and len(token) > 20)


def expand_turnstile_iframe(sb):
    """展开 Turnstile iframe，确保可见可点击"""
    sb.execute_script("""
        (function() {
            var iframes = document.querySelectorAll('iframe');
            iframes.forEach(function(f) {
                if (f.src && f.src.includes('challenges.cloudflare.com')) {
                    f.style.width = '300px';
                    f.style.height = '65px';
                    f.style.minWidth = '300px';
                    f.style.visibility = 'visible';
                    f.style.opacity = '1';
                }
            });
            // 确保 modal 内的 widget 容器足够大
            var widget = document.getElementById('captcha-widget');
            if (widget) {
                widget.style.minWidth = '300px';
                widget.style.minHeight = '65px';
                widget.style.overflow = 'visible';
            }
        })();
    """)


def handle_turnstile_modal(sb, timeout=120):
    """处理续期按钮点击后弹出的 Turnstile 验证码弹窗"""
    log("等待 Turnstile 弹窗出现...")

    # 1. 等待弹窗出现
    start = time.time()
    while time.time() - start < 15:
        if is_modal_open(sb):
            log("检测到验证码弹窗")
            break
        time.sleep(1)
    else:
        log("未检测到验证码弹窗（可能无需验证）", "WARN")
        return True

    sb.save_screenshot("screenshots/1a_modal.png")

    # 2. 等待 Turnstile iframe 加载
    log("等待 Turnstile widget 加载...")
    time.sleep(3)
    expand_turnstile_iframe(sb)
    sb.save_screenshot("screenshots/1b_turnstile.png")

    # 3. 尝试 SeleniumBase 内置方法
    log("尝试自动解决 Turnstile...")
    try:
        sb.uc_gui_handle_captcha()
        time.sleep(2)
        if has_turnstile_token(sb):
            log("Turnstile 已解决 (SeleniumBase UC)")
            time.sleep(2)
            return True
    except Exception as e:
        log(f"uc_gui_handle_captcha 异常: {e}")

    # 4. 内置方法未成功，尝试手动点击 iframe 内 checkbox
    log("尝试手动点击 Turnstile checkbox...")
    expand_turnstile_iframe(sb)

    for attempt in range(3):
        try:
            sb.uc_gui_click_captcha()
            log(f"已点击 Turnstile checkbox (第 {attempt + 1} 次)")
        except Exception as e:
            log(f"点击异常: {e}")

        # 等待解决
        for _ in range(10):
            time.sleep(2)
            if has_turnstile_token(sb):
                log("Turnstile 已解决!")
                time.sleep(2)
                return True
            if not is_modal_open(sb):
                log("弹窗已关闭（可能已通过）")
                time.sleep(2)
                return True

        expand_turnstile_iframe(sb)

    # 5. 最终等待
    log("等待 Turnstile 解决（最终轮）...")
    start = time.time()
    while time.time() - start < timeout:
        if has_turnstile_token(sb):
            log("Turnstile 已解决!")
            time.sleep(2)
            return True

        if not is_modal_open(sb):
            log("验证弹窗已关闭")
            time.sleep(2)
            return True

        time.sleep(2)
        expand_turnstile_iframe(sb)

    log("Turnstile 解决超时", "ERROR")
    sb.save_screenshot("screenshots/1c_turnstile_timeout.png")
    return False


# ==========================================
# g4f.gg 续期流程
# ==========================================
def _get_expiry(sb):
    return parse_expiry_from_page(sb.get_page_source() or "")


def do_renew(sb):
    os.makedirs("screenshots", exist_ok=True)

    # 0. 等待 CF Challenge 通过
    if not wait_for_page_ready(sb):
        sb.save_screenshot("screenshots/error_cf.png")
        raise RuntimeError("CF Challenge 未通过或页面加载失败")

    sb.save_screenshot("screenshots/0_loaded.png")
    page_source = sb.get_page_source() or ""
    expiry = parse_expiry_from_page(page_source)

    # 检查已达最大计时器
    if "already at the maximum timer" in page_source.lower():
        log("服务器已达最大计时器上限，无需续期")
        sb.save_screenshot("screenshots/2_result.png")
        send_tg(build_notification(success=True, expiry=expiry), "screenshots/2_result.png")
        return True

    # 检查冷却状态（用 DOM 检测而非字符串匹配，CSS 里的 .vote-cooldown{} 会误判）
    is_cooldown = sb.is_element_present(".vote-cooldown") or "you extended this server recently" in page_source.lower()

    if is_cooldown:
        log("检测到冷却状态，尝试切换 WARP IP 重试...")
        if rotate_warp_ip():
            log("WARP IP 已切换，重新加载页面...")
            sb.uc_open_with_reconnect(TARGET_URL, reconnect_time=8)
            time.sleep(random.uniform(5, 8))
            if wait_for_page_ready(sb):
                page_source = sb.get_page_source() or ""
                if sb.is_element_present(".vote-cooldown") or "you extended this server recently" in page_source.lower():
                    log("切换 IP 后仍为冷却状态，服务器冷却中")
                else:
                    log("切换 IP 后冷却消失，继续续期流程")
                    # 不 return，继续往下走续期流程
                    is_cooldown = False

        if is_cooldown:
            log("冷却中，服务器已在之前被续期，无需重复操作")
            sb.save_screenshot("screenshots/2_result.png")
            expiry = parse_expiry_from_page(sb.get_page_source() or "")
            send_tg(build_notification(success=True, expiry=expiry), "screenshots/2_result.png")
            return True

    # 1. 填入玩家名
    log(f"填入玩家名: {MC_USERNAME}")
    if not sb.is_element_visible(".name-input"):
        sb.save_screenshot("screenshots/error_no_input.png")
        raise RuntimeError("未找到输入框 .name-input")

    sb.type(".name-input", MC_USERNAME)
    time.sleep(random.uniform(0.5, 1.0))
    sb.save_screenshot("screenshots/1_filled.png")

    # 2. 点击续期按钮（会弹出 Turnstile 弹窗）
    log("点击续期按钮...")
    if not sb.is_element_visible(".vote-btn"):
        raise RuntimeError("未找到提交按钮 .vote-btn")

    sb.click(".vote-btn")
    log("已点击续期按钮，等待验证码弹窗...")
    time.sleep(2)

    # 3. 处理 Turnstile 弹窗
    if not handle_turnstile_modal(sb):
        sb.save_screenshot("screenshots/2_result.png")
        send_tg(build_notification(success=False, failure_reason="Turnstile 验证码未解决"),
                "screenshots/2_result.png")
        return False

    # 4. Turnstile 解决后，JS 自动提交表单，等待页面刷新
    log("等待表单提交和页面刷新...")
    time.sleep(8)

    # 5. 读取结果
    page_source = sb.get_page_source() or ""
    sb.save_screenshot("screenshots/2_result.png")
    page_text_lower = page_source.lower()

    if "3 hours added" in page_text_lower or "thanks for supporting" in page_text_lower:
        log("续期成功！+3 小时")
        expiry = parse_expiry_from_page(page_source)
        send_tg(build_notification(success=True, expiry=expiry), "screenshots/2_result.png")
        return True

    if "already at the maximum timer" in page_text_lower:
        log("服务器已达最大计时器上限，无需续期")
        expiry = parse_expiry_from_page(page_source)
        send_tg(build_notification(success=True, expiry=expiry), "screenshots/2_result.png")
        return True

    if "you extended this server recently" in page_text_lower or sb.is_element_present(".vote-cooldown"):
        log("冷却中，服务器已在之前被续期")
        expiry = parse_expiry_from_page(page_source)
        send_tg(build_notification(success=True, expiry=expiry), "screenshots/2_result.png")
        return True

    if "flash-error" in page_source or "something went wrong" in page_text_lower:
        try:
            err_el = sb.find_element(".flash-error")
            err_text = err_el.text.strip() if err_el else "未知错误"
        except Exception:
            err_text = "未知错误"
        log(f"失败提示: {err_text}", "WARN")
        send_tg(build_notification(success=False, failure_reason=err_text), "screenshots/2_result.png")
        return False

    log("未读取到明确成功/失败标志，请查阅截图确认", "WARN")
    send_tg(build_notification(success=False, failure_reason="未检测到成功标志"), "screenshots/2_result.png")
    return False


# ==========================================
# 主入口
# ==========================================
def main():
    log("===== G4F 自动续期 (SeleniumBase UC + Turnstile) =====")

    vdisplay = Xvfb(width=1280, height=720, colordepth=24)
    vdisplay.start()

    try:
        with SB(
            uc=True,
            test=True,
            headless=False,
            locale="en",
            chromium_arg="--disable-dev-shm-usage,--no-sandbox,--disable-gpu,"
                         "--disable-software-rasterizer,--disable-background-timer-throttling,"
                         "--window-size=1280,720"
        ) as sb:
            log(f"访问: {TARGET_URL}")
            sb.uc_open_with_reconnect(TARGET_URL, reconnect_time=8)
            time.sleep(random.uniform(5, 8))

            # 模拟人类行为：滚动
            try:
                for _ in range(3):
                    scroll_y = random.randint(200, 600)
                    sb.execute_script(f"window.scrollBy(0, {scroll_y});")
                    time.sleep(random.uniform(0.5, 1.5))
                time.sleep(random.uniform(1.0, 2.0))
            except Exception:
                pass

            do_renew(sb)

    except Exception as e:
        import traceback
        log(f"致命错误: {e}", "ERROR")
        traceback.print_exc()
        os.makedirs("screenshots", exist_ok=True)
        send_tg(build_notification(success=False, failure_reason=str(e)[:200]))
        sys.exit(1)
    finally:
        vdisplay.stop()


if __name__ == "__main__":
    main()
