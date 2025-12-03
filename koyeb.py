import os
import json
import time
import logging
import requests
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta

# 配置日志格式
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def validate_env_variables():
    """验证环境变量"""
    koyeb_accounts_env = os.getenv("KOYEB_ACCOUNTS")
    if not koyeb_accounts_env:
        raise ValueError("❌ KOYEB_ACCOUNTS 环境变量未设置或格式错误")
    try:
        return json.loads(koyeb_accounts_env)
    except json.JSONDecodeError:
        raise ValueError("❌ KOYEB_ACCOUNTS JSON 格式无效")

def send_tg_message(message):
    """发送 Telegram 消息"""
    bot_token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")

    if not bot_token or not chat_id:
        logging.warning("⚠️ TG_BOT_TOKEN 或 TG_CHAT_ID 未设置，跳过 Telegram 通知")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    def _post(payload):
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return response

    try:
        try:
            _post({"chat_id": chat_id, "text": message, "parse_mode": "Markdown"})
            logging.info("✅ Telegram 消息发送成功")
        except requests.HTTPError as http_err:
            response = getattr(http_err, "response", None)
            if response is not None and response.status_code == 400:
                detail = response.text.strip()
                logging.warning(f"⚠️ Telegram 返回 400，改用纯文本重试: {detail[:200]}")
                _post({"chat_id": chat_id, "text": message})
                logging.info("✅ Telegram 消息发送成功（纯文本重试）")
            else:
                raise
    except requests.RequestException as e:
        logging.error(f"❌ 发送 Telegram 消息失败: {e}")

def login_koyeb(email, password):
    """执行 Koyeb 账户登录"""
    if not email or not password:
        return False, "邮箱或密码为空"

    login_url = "https://app.koyeb.com/v1/account/login"
    login_page_url = "https://app.koyeb.com/auth/login"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://app.koyeb.com",
        "Referer": "https://app.koyeb.com/auth/login",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    }
    data = {"email": email.strip(), "password": password}

    session = requests.Session()
    session.headers.update(headers)

    signin_fallback = None

    try:
        # 先请求登录页以获取必要的 Cookie，减少 403 风险，并捕获跳转的 WorkOS 登录信息
        preload = session.get(login_page_url, timeout=30, allow_redirects=True)
        preload.raise_for_status()

        final_url = preload.url
        parsed = urlparse(final_url)
        if "signin.koyeb.com" in parsed.netloc:
            qs = parse_qs(parsed.query)
            signin_fallback = {
                "base": f"{parsed.scheme}://{parsed.netloc}",
                "referer": final_url,
                "client_id": qs.get("client_id", [None])[0],
                "redirect_uri": qs.get("redirect_uri", [None])[0],
                "state": qs.get("state", [None])[0],
                "authorization_session_id": qs.get("authorization_session_id", [None])[0],
            }
    except requests.RequestException as e:
        logging.warning(f"⚠️ 预检登录页失败，继续尝试登录: {e}")

    try:
        response = session.post(login_url, json=data, timeout=30)
        if response.status_code == 403 and signin_fallback:
            detail = response.text.strip()[:200]
            logging.info(f"ℹ️ 旧接口 403，尝试 WorkOS 登录: {detail}")

            workos_url = f"{signin_fallback['base']}"
            payload = {
                "email": data["email"],
                "password": password,
                "client_id": signin_fallback["client_id"],
                "redirect_uri": signin_fallback["redirect_uri"],
                "state": signin_fallback["state"],
                "authorization_session_id": signin_fallback["authorization_session_id"],
            }

            workos_headers = {**headers, "Referer": signin_fallback["referer"]}

            workos_resp = session.post(
                workos_url,
                json=payload,
                timeout=30,
                allow_redirects=True,
                headers=workos_headers,
            )

            if workos_resp.is_redirect:
                callback_url = workos_resp.headers.get("location")
                if callback_url:
                    session.get(callback_url, headers=workos_headers, timeout=30)

            if workos_resp.ok or workos_resp.status_code in (302, 303):
                return True, "WorkOS 登录成功"

            fallback_detail = workos_resp.text.strip()
            return False, f"WorkOS 登录失败: HTTP {workos_resp.status_code} {fallback_detail[:200]}"

        if response.status_code == 403:
            detail = response.text.strip()
            detail = detail[:200] + "..." if len(detail) > 200 else detail
            return False, f"403 Forbidden（可能需要验证码或 Cookie）: {detail}"

        response.raise_for_status()
        return True, "成功"
    except requests.Timeout:
        return False, "请求超时"
    except requests.RequestException as e:
        return False, str(e)

def main():
    """主流程"""
    try:
        koyeb_accounts = validate_env_variables()
        if not koyeb_accounts:
            raise ValueError("❌ 没有找到有效的 Koyeb 账户信息")

        # 获取北京时间（UTC+8）
        current_time = (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
        messages = []

        for account in koyeb_accounts:
            email = account.get("email", "").strip()
            password = account.get("password", "")

            if not email or not password:
                logging.warning(f"⚠️ 账户信息不完整，跳过: {email}")
                continue

            logging.info(f"🔄 正在处理账户: {email}")
            success, message = login_koyeb(email, password)

            result = "🎉 登录结果: 成功" if success else f"❌ 登录失败 | 原因: {message}"
            messages.append(f"📧 账户: {email}\n\n{result}")

            time.sleep(5)

        summary = f"🗓️ 北京时间: {current_time}\n\n" + "\n\n".join(messages) + "\n\n✅ 任务执行完成"

        logging.info("📋 任务完成，发送 Telegram 通知")
        send_tg_message(summary)

    except Exception as e:
        error_message = f"❌ 执行出错: {e}"
        logging.error(error_message)
        send_tg_message(error_message)

if __name__ == "__main__":
    main()
