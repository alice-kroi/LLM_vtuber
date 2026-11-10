# ============================================================
# tool/ocr_cloud.py — 火山引擎 OCR（云端识别，图片 + PDF）
# ============================================================
# 用途：
#   - 屏幕/窗口截图兜底识别（UIA 认不出控件时）
#   - 扫描版 PDF 逐页转文字，供知识库入库
#
# 走「通用文字识别 OCRNormal」（visual.volcengineapi.com，2020-08-26）：
#   - 图片 base64 直传，无需公网 URL
#   - 单张 ≤5MB，中英文
#   - 计费约 0.0035 元/次
#
# 凭证（环境变量优先，其次 config.ini [ocr]）：
#   VOLC_ACCESSKEY / VOLC_SECRETKEY（也接受 VOLC_AK / VOLC_SK）
#   注意：这是火山引擎 AK/SK，不是方舟的 Doubao_API_KEY。
# ============================================================

from __future__ import annotations

import asyncio
import base64
import configparser
import io
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
_MAX_BYTES = 4 * 1024 * 1024      # 官方上限 5MB，留点余量
_MAX_SIDE = 3840                  # 长边上限，超过则等比缩小

_service = None
_lock = threading.RLock()


# ---------- 配置 ----------

def _load_env_file() -> Dict[str, str]:
    """读取项目根目录 .env（不依赖 python-dotenv，缺失/损坏时静默跳过）"""
    path = _ROOT / ".env"
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw = line.split("=", 1)
            values[key.strip()] = raw.strip().strip('"').strip("'")
    except Exception:  # noqa: BLE001
        pass
    return values


def _settings() -> Dict[str, Any]:
    cfg = configparser.ConfigParser()
    path = _ROOT / "config.ini"
    if path.exists():
        try:
            cfg.read(path, encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    section = "ocr"
    dotenv = _load_env_file()

    def pick(*keys: str) -> str:
        for key in keys:
            for source in (os.getenv(key), dotenv.get(key)):
                if source:
                    return source.strip()
        return ""

    # Access Key 签名（OCRNormal）
    ak = (pick("VOLC_ACCESSKEY", "VOLC_AK")
          or cfg.get(section, "access_key", fallback="")).strip()
    sk = (pick("VOLC_SECRETKEY", "VOLC_SK")
          or cfg.get(section, "secret_key", fallback="")).strip()
    # API Key（Bearer，走 AI MediaKit）
    api_key = (pick("VOLC_API_KEY", "MEDIAKIT_API_KEY")
               or cfg.get(section, "api_key", fallback="")).strip()
    api_key_id = pick("VOLC_API_KEY_ID")
    provider = (pick("VOLC_OCR_PROVIDER")
                or cfg.get(section, "provider", fallback="auto")).strip().lower()
    return {
        "access_key": ak,
        "secret_key": sk,
        "api_key": api_key,
        "api_key_id": api_key_id,
        "provider": provider,
        "mode": cfg.get(section, "mode", fallback="default").strip() or "default",
        "lang_type": (pick("VOLC_OCR_LANG_TYPE")
                      or cfg.get(section, "lang_type", fallback="default")).strip()
                     or "default",
        "filter_thresh": cfg.get(section, "filter_thresh", fallback="80").strip() or "80",
        "timeout": _float(cfg.get(section, "timeout", fallback="15"), 15.0),
        "dpi": _int(cfg.get(section, "pdf_dpi", fallback="200"), 200),
        "max_pages": _int(cfg.get(section, "pdf_max_pages", fallback="100"), 100),
    }


def _float(raw: Any, default: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _int(raw: Any, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def available() -> bool:
    """凭证是否就绪（不代表额度/权限正常）"""
    s = _settings()
    return bool(s["api_key"] or (s["access_key"] and s["secret_key"]))


def provider() -> str:
    """实际使用的线路：volc（AK/SK，base64 直传）/ mediakit（API Key）/ none"""
    s = _settings()
    choice = s["provider"]
    if choice in ("mediakit", "volc"):
        return choice
    # auto：优先 AK/SK（本地图直接 base64，无需上传中转），否则用 API Key
    if s["access_key"] and s["secret_key"]:
        return "volc"
    if s["api_key"]:
        return "mediakit"
    return "none"


def credentials_hint() -> str:
    return ("未配置 OCR 凭证。两种任选其一：\n"
            "  1) API Key（推荐，Bearer）：VOLC_API_KEY\n"
            "  2) IAM Access Key 签名：VOLC_ACCESSKEY / VOLC_SECRETKEY\n"
            "注意都不是方舟的 Doubao_API_KEY")


# ---------- 客户端 ----------

def _get_service():
    """懒加载单例 VisualService（AK/SK 签名）"""
    global _service
    with _lock:
        if _service is not None:
            return _service
        try:
            from volcengine.visual.VisualService import VisualService
        except ImportError as e:  # noqa: BLE001
            raise RuntimeError(f"volcengine SDK 未安装（pip install volcengine）: {e}")
        s = _settings()
        if not (s["access_key"] and s["secret_key"]):
            raise RuntimeError(credentials_hint())
        svc = VisualService()
        svc.set_ak(s["access_key"])
        svc.set_sk(s["secret_key"])
        svc.set_connection_timeout(s["timeout"])
        svc.set_socket_timeout(s["timeout"])
        _service = svc
        return svc


def reset() -> None:
    """配置变更后重置客户端（测试/热更新用）"""
    global _service
    with _lock:
        _service = None


# ---------- AI MediaKit（API Key / Bearer） ----------
# 本地图片没有公网 URL：先申请上传地址 → PUT 上传 → 用 mediakit://<file_id> 提交识别

MEDIAKIT_BASE = "https://mediakit.cn-beijing.volces.com/api/v1"
_MEDIAKIT_HINTS = {
    "AmkAbilityNotFound":
        "AI MediaKit 能力未开通：控制台 → 智能处理 / AI MediaKit → 开通服务",
    "InvalidAPIKey":
        "API Key 无效：检查 VOLC_API_KEY 是否填的是「API Key Secret」而不是 ID",
    "AccessDenied": "凭证被拒绝，检查 API Key 归属账号是否有 OCR 权限",
}


def _mediakit_error(resp: Dict[str, Any]) -> str:
    err = (resp or {}).get("error") or {}
    msg = str(err.get("message") or resp.get("message") or "")
    for code, hint in _MEDIAKIT_HINTS.items():
        if code in msg or code == err.get("code"):
            return f"{hint}（{msg[:140]}）"
    return msg[:200] or "未知错误"


def _mediakit_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    s = _settings()
    if not s["api_key"]:
        return {"error": {"message": "InvalidAPIKey: 未配置 VOLC_API_KEY"}}
    url = f"{MEDIAKIT_BASE}{path}"
    headers = {"Authorization": f"Bearer {s['api_key']}",
               "Content-Type": "application/json"}
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=s["timeout"]) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {"error": {"message": f"HTTP {e.code}: {e.reason}"}}
    except Exception as e:  # noqa: BLE001
        return {"error": {"message": f"{type(e).__name__}: {e}"}}


def _mediakit_upload(data: bytes, mime: str = "image/jpeg") -> tuple:
    """申请上传地址 → PUT 二进制 → 返回 (file_id, error)"""
    s = _settings()
    resp = _mediakit_post("/tools-sync/request-media-upload-url", {})
    if not resp.get("success"):
        return "", _mediakit_error(resp)
    result = resp.get("result") or {}
    file_id = str(result.get("file_id") or "")
    upload_url = str(result.get("upload_url") or "")
    if not (file_id and upload_url):
        return "", "上传地址返回不完整"
    headers = {"Content-Type": mime}
    for item in result.get("upload_headers") or []:
        if isinstance(item, dict) and item.get("key"):
            headers[str(item["key"])] = str(item.get("value") or "")
    req = urllib.request.Request(upload_url, data=data, headers=headers,
                                 method=str(result.get("method") or "PUT"))
    try:
        with urllib.request.urlopen(req, timeout=max(30.0, s["timeout"])) as r:
            r.read()
    except urllib.error.HTTPError as e:
        return "", f"上传失败 HTTP {e.code}: {e.reason}"
    except Exception as e:  # noqa: BLE001
        return "", f"上传失败 {type(e).__name__}: {e}"
    return file_id, ""


def _parse_mediakit(resp: Dict[str, Any]) -> Dict[str, Any]:
    if not resp.get("success"):
        return {"success": False, "error": _mediakit_error(resp)}
    items = (resp.get("result") or {}).get("ocr_result") or []
    lines: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        lines.append({
            "text": str(item.get("content") or ""),
            "conf": float(item.get("confidence") or 0.9),
            "x1": int(item.get("top_left_x") or 0),
            "y1": int(item.get("top_left_y") or 0),
            "x2": int(item.get("bottom_right_x") or 0),
            "y2": int(item.get("bottom_right_y") or 0),
        })
    return {"success": True, "provider": "mediakit", "lines": lines,
            "count": len(lines),
            "text": "\n".join(ln["text"] for ln in lines),
            "request_id": resp.get("request_id", "")}


def _ocr_mediakit(data: bytes) -> Dict[str, Any]:
    file_id, err = _mediakit_upload(data)
    if not file_id:
        return {"success": False, "error": err}
    resp = _mediakit_post("/tools-sync/image-ocr", {"image_url": file_id})
    result = _parse_mediakit(resp)
    if not result.get("success"):
        logger.warning(f"[ocr_cloud] mediakit 识别失败: {result.get('error')}")
    return result


# ---------- 图片识别 ----------

def _shrink_to_limit(data: bytes) -> bytes:
    """超过体积/分辨率上限时降采样重编码（尽量保住文字）"""
    try:
        from PIL import Image
    except ImportError:  # noqa: BLE001
        return data
    try:
        img = Image.open(io.BytesIO(data))
        changed = False
        if max(img.size) > _MAX_SIDE:
            ratio = _MAX_SIDE / max(img.size)
            img = img.resize((max(1, int(img.width * ratio)),
                              max(1, int(img.height * ratio))))
            changed = True
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=90)
        out = buf.getvalue()
        if changed or len(out) < len(data):
            return out
    except Exception:  # noqa: BLE001
        return data
    return data


def _order_lines(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按阅读顺序重排：先按纵坐标分行（容差=行高 60%），行内再按横坐标。

    云端返回的是检测顺序，教材/表格版式下直接拼接会读成乱序，影响入库质量。
    """
    if not lines:
        return lines
    ordered = sorted(lines, key=lambda ln: (ln["y1"], ln["x1"]))
    rows: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    row_y = None
    for line in ordered:
        height = max(1, int(line["y2"]) - int(line["y1"]))
        if row_y is None or abs(line["y1"] - row_y) <= height * 0.6:
            current.append(line)
            if row_y is None:
                row_y = line["y1"]
        else:
            rows.append(current)
            current, row_y = [line], line["y1"]
    if current:
        rows.append(current)
    out: List[Dict[str, Any]] = []
    for row in rows:
        out.extend(sorted(row, key=lambda ln: ln["x1"]))
    return out


def _parse_normal(resp: Dict[str, Any]) -> Dict[str, Any]:
    """解析 OCRNormal 返回：data.line_texts + data.line_rects"""
    if not isinstance(resp, dict):
        return {"success": False, "error": f"返回结构异常: {type(resp).__name__}"}
    code = resp.get("code")
    if code not in (10000, None):
        return {"success": False, "error": f"code={code} {resp.get('message') or ''}".strip(),
                "raw_code": code}
    data = resp.get("data") or {}
    texts = data.get("line_texts") or []
    rects = data.get("line_rects") or []
    chars = data.get("chars") or []
    lines: List[Dict[str, Any]] = []
    for i, text in enumerate(texts):
        rect = rects[i] if i < len(rects) else {}
        conf = 0.9
        row = chars[i] if i < len(chars) else None
        if isinstance(row, list) and row:
            scores = [c.get("score") for c in row
                      if isinstance(c, dict) and c.get("score") is not None]
            if scores:
                conf = round(sum(float(s) for s in scores) / len(scores), 4)
        x = int(rect.get("x", 0))
        y = int(rect.get("y", 0))
        w = int(rect.get("width", 0))
        h = int(rect.get("height", 0))
        lines.append({"text": str(text), "conf": conf,
                      "x1": x, "y1": y, "x2": x + w, "y2": y + h})
    lines = _order_lines(lines)
    return {"success": True, "provider": "volc_visual", "lines": lines,
            "count": len(lines),
            "text": "\n".join(ln["text"] for ln in lines),
            "request_id": resp.get("request_id", "")}


def ocr_image_base64(b64: str, mode: Optional[str] = None,
                     filter_thresh: Optional[str] = None) -> Dict[str, Any]:
    """同步识别 base64 图片（不要传 data: 前缀）"""
    if not b64:
        return {"success": False, "error": "空图片"}
    s = _settings()
    try:
        svc = _get_service()
        form = {
            "image_base64": b64,
            "type": s["lang_type"],
            "mode": mode or s["mode"],
            "filter_thresh": filter_thresh or s["filter_thresh"],
        }
        resp = svc.ocr_normal(form)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"{type(e).__name__}: {e}"}
    result = _parse_normal(resp)
    if not result.get("success"):
        logger.warning(f"[ocr_cloud] 识别失败: {result.get('error')}")
    return result


def ocr_image_bytes(data: bytes, mode: Optional[str] = None) -> Dict[str, Any]:
    """同步识别图片字节：按 provider 选 AI MediaKit（API Key）或 OCRNormal（AK/SK）"""
    if not data:
        return {"success": False, "error": "空图片"}
    data = _shrink_to_limit(data)
    line = provider()
    if line == "mediakit":
        return _ocr_mediakit(data)
    if line == "volc":
        if len(data) > _MAX_BYTES:
            return {"success": False,
                    "error": f"图片 {len(data)} 字节超过云端上限，请先缩小"}
        return ocr_image_base64(base64.b64encode(data).decode("ascii"), mode=mode)
    return {"success": False, "error": credentials_hint()}


def ocr_pil_image(img, mode: Optional[str] = None) -> Dict[str, Any]:
    """识别 PIL.Image（屏幕截图常用）"""
    try:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=90)
        return ocr_image_bytes(buf.getvalue(), mode=mode)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"图片编码失败: {e}"}


# ---------- 异步包装（不阻塞事件循环） ----------

async def aocr_image_bytes(data: bytes, mode: Optional[str] = None) -> Dict[str, Any]:
    return await asyncio.to_thread(ocr_image_bytes, data, mode)


async def aocr_pil_image(img, mode: Optional[str] = None) -> Dict[str, Any]:
    return await asyncio.to_thread(ocr_pil_image, img, mode)


# ---------- PDF（扫描版入库） ----------

def ocr_pdf_pages(pdf_path: Any, max_pages: Optional[int] = None,
                  dpi: Optional[int] = None,
                  on_page: Optional[Callable[[int, int, Dict[str, Any]], None]] = None
                  ) -> Dict[str, Any]:
    """扫描版 PDF 逐页 OCR：渲染成图 → 云端识别。

    Returns:
        {success, page_count, pages:[{page, text, lines}], text, error}
    """
    path = Path(pdf_path)
    if not path.is_file():
        return {"success": False, "error": f"文件不存在: {path}"}
    try:
        import pymupdf as fitz  # type: ignore
    except ImportError:
        try:
            import fitz  # type: ignore
        except ImportError as e:  # noqa: BLE001
            return {"success": False, "error": f"PyMuPDF 不可用: {e}"}
    s = _settings()
    max_pages = max_pages or s["max_pages"]
    dpi = dpi or s["dpi"]
    pages: List[Dict[str, Any]] = []
    errors: List[str] = []
    doc = fitz.open(str(path))
    try:
        total = len(doc)
        for i in range(min(total, max_pages)):
            page = doc[i]
            try:
                pix = page.get_pixmap(dpi=dpi)
                result = ocr_image_bytes(pix.tobytes("png"))
            except Exception as e:  # noqa: BLE001
                result = {"success": False, "error": str(e)}
            entry = {
                "page": i + 1,
                "text": result.get("text") if result.get("success") else "",
                "lines": result.get("lines") or [],
                "success": bool(result.get("success")),
            }
            if not entry["success"]:
                errors.append(f"第 {i + 1} 页: {result.get('error')}")
            pages.append(entry)
            if on_page is not None:
                try:
                    on_page(i + 1, total, result)
                except Exception:  # noqa: BLE001
                    pass
    finally:
        doc.close()
    merged = "\n\n".join(
        f"--- 第 {p['page']} 页 ---\n{p['text']}" for p in pages if p["text"])
    return {
        "success": bool(pages) and not errors,
        "page_count": len(pages),
        "total_pages": total,
        "pages": pages,
        "text": merged,
        "error": "; ".join(errors[:3]),
    }


__all__ = [
    "available", "provider", "credentials_hint", "reset",
    "ocr_image_base64", "ocr_image_bytes", "ocr_pil_image",
    "aocr_image_bytes", "aocr_pil_image", "ocr_pdf_pages",
]
