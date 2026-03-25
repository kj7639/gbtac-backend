from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

# import routers here
from helpers.rate_limit import limiter
from routers.graphs import router as graph_router
from routers.energy import router as energy_router
from routers.auth import router as auth_router
from routers.report import router as report_router
from routers.content import router as content_router

app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware( CORSMiddleware, allow_origins=["http://localhost:3000"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"], )

# add routers here
app.include_router(graph_router)
app.include_router(energy_router)
app.include_router(auth_router)
app.include_router(report_router)
app.include_router(content_router)

@app.get("/")
async def root():
    return "GBTAC API"

# to run: uvicorn main:app --reload