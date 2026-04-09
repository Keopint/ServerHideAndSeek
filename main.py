from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from db import init_db
from routes.gameRoutes import game_router
from routes.infoRoutes import info_router
from routes.websocketRoutes import register_websocket_endpoint

app = FastAPI(title="GeoGame Server", version="1.0.0")
app.include_router(game_router)
app.include_router(info_router)


# CORS для Android приложения
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Регистрируем WebSocket эндпоинт
register_websocket_endpoint(app)

# ---------- REST API ----------
@app.get("/")
async def root():
    return {"message": "GeoGame Server", "status": "running"}

# ---------- Запуск ----------
@app.on_event("startup")
async def startup():
    await init_db()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)