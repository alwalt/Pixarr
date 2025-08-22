# app/main.py — only app wiring, no endpoints here.
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# import routers
from app.api.routes import review, staging

app = FastAPI(title="Pixarr API", version="0.3")

# CORS (allow Vite dev)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
app.include_router(review.api_router, prefix="/api")
app.include_router(staging.api_router, prefix="/api")

# public (non-API) routers for serving files/thumbs
app.include_router(review.public_router)   # /media/* and /thumb/review/*
app.include_router(staging.public_router)  # /staging/* and /thumb/staging/*
