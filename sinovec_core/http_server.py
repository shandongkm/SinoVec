# ── SinoVec HTTP API 服务器 ─────────────────────────────────────────
"""
轻量级 HTTP API server，不依赖任何第三方库。
供 OpenClaw Active Memory 插件调用。
"""
import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Optional
from urllib.parse import parse_qs, urlparse

from sinovec_core.search import cmd_search

logger = logging.getLogger(__name__)


def _run_http_server(host: str = "127.0.0.1", port: int = 18793) -> None:
    """轻量级 HTTP API server（多线程）。"""
    server = ThreadedHTTPServer((host, port), _MemoryHandler)
    logger.info(f"HTTP API 服务器启动 {host}:{port}")
    print(f"✅ SinoVec HTTP API 已启动 http://{host}:{port}")
    print(f"   搜索: GET /search?q=关键词&top_k=3")
    print(f"   健康: GET /health")
    print(f"   按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止 HTTP 服务器...")
        server.shutdown()


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class _MemoryHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器"""

    def _check_auth(self) -> bool:
        """
        校验 API Key，支持 Bearer token 或 X-API-Key header。
        安全说明：未配置 API Key 时仅允许 /health 探活端点，其他端点拒绝访问。
        生产环境必须配置 MEMORY_API_KEY。
        """
        expected = os.getenv("MEMORY_API_KEY", "")
        if not expected:
            # 未配置 API Key 时，仅允许 /health（无敏感数据），其他端点拒绝
            # 这是合理的运维需求：负载均衡器/Docker HEALTHCHECK 需要能探活
            if self.path == "/health":
                return True
            logger.error("MEMORY_API_KEY 未配置，仅 /health 可访问，其他端点已拒绝")
            return False
        # 1. Authorization: Bearer <key>
        auth_header = self.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip()
        if token and hmac.compare_digest(token, expected):
            return True
        # 2. X-API-Key: <key>
        token = self.headers.get("X-API-Key", "").strip()
        if token and hmac.compare_digest(token, expected):
            return True
        # 3. URL 参数已移除（安全修复：会被日志记录，禁止用于传递 Key）
        return False

    def _send_json(self, data: dict, status: int = 200) -> None:
        """统一 JSON 响应"""
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_GET(self) -> None:
        if self.path != "/health" and not self._check_auth():
            self._send_json({"error": "unauthorized"}, 401)
            return

        parsed = urlparse(self.path)

        if parsed.path == "/health":
            self._send_json({"status": "ok"})

        elif parsed.path == "/search":
            params = parse_qs(parsed.query)
            query = params.get("q", [""])[0]
            if not query:
                self._send_json({"error": "missing q param"}, 400)
                return
            try:
                top_k = int(params.get("top_k", ["3"])[0])
                top_k = max(1, min(top_k, 100))
            except (ValueError, TypeError):
                top_k = 3
            user_id = params.get("user_id", [None])[0]
            use_rerank = params.get("rerank", ["1"])[0] != "0"
            use_expand = params.get("expand", ["1"])[0] != "0"
            try:
                results = cmd_search(
                    query,
                    top_k=top_k,
                    user_id=user_id,
                    use_rerank=use_rerank,
                    use_expand=use_expand,
                )
                for r in results:
                    r["source"] = "memory"
                self._send_json({"count": len(results), "results": results})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif parsed.path == "/stats":
            # 注意：统一入口 do_GET 已在路径匹配前完成 auth 检查，此处无需重复
            try:
                from sinovec_core.db import get_conn
                with get_conn() as conn:
                    cur = conn.cursor()
                    try:
                        cur.execute(
                            "SELECT COUNT(*), SUM(recall_count), MAX(recall_count) "
                            "FROM sinovec"
                        )
                        total, recall_sum, recall_max = cur.fetchone()
                        cur.execute(
                            "SELECT COUNT(*) FROM sinovec "
                            "WHERE last_access_time > NOW() - INTERVAL '24 hours'"
                        )
                        hot_24h = cur.fetchone()[0]
                    finally:
                        cur.close()
                self._send_json({
                    "total": total or 0,
                    "recall_total": recall_sum or 0,
                    "recall_max": recall_max or 0,
                    "hot_24h": hot_24h or 0,
                })
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif parsed.path == "/metrics":
            # 注意：统一入口 do_GET 已在路径匹配前完成 auth 检查，此处无需重复
            try:
                from sinovec_core.db import get_conn
                with get_conn() as conn:
                    cur = conn.cursor()
                    try:
                        cur.execute(
                            "SELECT COUNT(*) FROM sinovec "
                            "WHERE last_access_time > NOW() - INTERVAL '1 hour'"
                        )
                        recent = cur.fetchone()[0]
                    finally:
                        cur.close()
                self._send_json({"request_count": recent or 0})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if not self._check_auth():
            self._send_json({"error": "unauthorized"}, 401)
            return

        parsed = urlparse(self.path)

        if parsed.path == "/memory":
            content_len_str = self.headers.get("Content-Length", "")
            try:
                content_len = max(0, min(int(content_len_str), 1024 * 1024))  # 上限 1MB
                body = self.rfile.read(content_len)
                data = json.loads(body.decode("utf-8"))
            except (ValueError, IOError, json.JSONDecodeError):
                self._send_json({"error": "invalid request body"}, 400)
                return

            text = data.get("text", "").strip()
            if not text:
                self._send_json({"error": "text is required"}, 400)
                return

            user_id = data.get("user_id", "主人")
            try:
                from sinovec_core.commands import cmd_add
                mem_id = cmd_add(text, user=user_id)
                self._send_json({"id": mem_id, "status": "added"}, 201)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
        else:
            self._send_json({"error": "not found"}, 404)

    def log_message(self, format, *args) -> None:
        pass  # 抑制 HTTP 日志
