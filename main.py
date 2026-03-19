import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.database_config import Base, engine
from routes.agent_routes import router as agent_router
from routes.user_routes import router as auth_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create database tables
    Base.metadata.create_all(bind=engine)
    yield
    # Shutdown: Cleanup if needed (Vercel allows max 500ms for cleanup)
    pass


app = FastAPI(
    title="CortexAI API",
    lifespan=lifespan
)

cors_origins = os.getenv("BACKEND_CORS_ORIGINS", "http://localhost:5173")
origins = [o.strip() for o in cors_origins.split(",") if o.strip()]

# If no origins specified, allow common localhost variants for development
if not origins:
    origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.include_router(auth_router)
app.include_router(agent_router)
# standerd response for the root path
@app.get("/")
async def root():
    return {
        "message": "Welcome to the CortexAI API",
        "version": "1.0.0",
        "status": "success"
    }


if __name__ == "__main__":
    import uvicorn 

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=True,
    )
