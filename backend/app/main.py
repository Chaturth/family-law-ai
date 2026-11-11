from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from . import db
from .routes import api

db.init()
app = FastAPI(title="Family Law AI Agent System (Dispute Architect AI)", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(api, prefix="/api")
static = Path(__file__).resolve().parent.parent / "static"
app.mount("/", StaticFiles(directory=static, html=True), name="static")
