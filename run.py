import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=False,
        proxy_headers=False,
        access_log=False,
        ws_max_size=1024 * 1024,
    )
