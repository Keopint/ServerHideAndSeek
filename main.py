from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from database.db import init_db
from routes.gameRoutes import game_router
from routes.infoRoutes import info_router
from routes.websocketRoutes import register_websocket_endpoint


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    register_websocket_endpoint(app)
    from services.timer_recovery import recover_phase_timers, recover_zone_and_effect_timers
    await recover_phase_timers()
    await recover_zone_and_effect_timers()
    yield


app = FastAPI(title="GeoGame Server", version="1.0.0", lifespan=lifespan)
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

# ---------- REST API ----------
@app.get("/")
async def root():
    return {"message": "GeoGame Server", "status": "running"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)

