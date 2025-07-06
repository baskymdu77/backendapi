from fastapi import FastAPI
from app.routes import hello, openai_api, home_depot_api
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os

load_dotenv()
api_key = os.environ.get("OPENAI_API_KEY")
print(api_key)

app = FastAPI(
    title="Construction Management API",
    description="API for construction management with OpenAI integration",
    version="0.1.0"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Include routers
app.include_router(hello.router)
app.include_router(openai_api.router)
app.include_router(home_depot_api.router)