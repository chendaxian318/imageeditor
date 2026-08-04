"""Explicit offline-first privacy policy used by settings and tests."""

NETWORK_POLICY = "offline"
BACKGROUND_UPLOADS_ENABLED = False
UPDATE_CHECK_MODE = "manual"


def privacy_summary(locale: str = "zh-CN") -> str:
    if locale == "en-US":
        return "Images stay on this computer. No background upload or automatic network request."
    return "图片仅在本机处理；不会后台上传，也不会自动发起网络请求。"
