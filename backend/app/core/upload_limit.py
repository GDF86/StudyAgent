from starlette.responses import JSONResponse


class UploadLimitMiddleware:
    """Bound multipart bodies even without Content-Length, before multipart parsing."""

    def __init__(self, app, limit: int):
        self.app, self.limit = app, limit + 64 * 1024

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or not scope["path"].endswith("/documents"):
            return await self.app(scope, receive, send)
        parts, size = [], 0
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            body = event.get("body", b"")
            size += len(body)
            if size > self.limit:
                response = JSONResponse(status_code=413, content={"code": "FILE_TOO_LARGE", "message": "上传文件超过大小限制", "detail": None})
                return await response(scope, receive, send)
            parts.append(body)
            if not event.get("more_body", False):
                break
        sent = False

        async def bounded_receive():
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": b"".join(parts), "more_body": False}

        await self.app(scope, bounded_receive, send)
