import os
from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
import httpx

app = FastAPI()

@app.get("/")
def home():
    path = "index.html" if os.path.exists("index.html") else "static/index.html"
    return FileResponse(path)

@app.get("/proxy")
async def proxy(url: str):
    async with httpx.AsyncClient(follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        return Response(content=r.text, media_type="text/html")

