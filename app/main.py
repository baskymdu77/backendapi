from fastapi import FastAPI
from app.routes import hello, openai_api, home_depot_api, home_depot_search, stripe_api
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os
from app.services.firebase_admin import init_firebase

load_dotenv()
api_key = os.environ.get("OPENAI_API_KEY")
print(api_key)

app = FastAPI(
    title="Construction Management API",
    description="API for construction management with OpenAI integration",
    version="0.1.0",
    # Add a prefix to all routes
    root_path="/api/v1"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Initialize Firebase on startup
@app.on_event("startup")
def on_startup():
    try:
        init_firebase()
    except Exception as e:
        # Avoid crashing the app if Firebase isn't configured yet
        print(f"Firebase initialization error: {e}")

# Include routers
app.include_router(hello.router)
app.include_router(openai_api.router)
app.include_router(home_depot_api.router)
app.include_router(home_depot_search.router)
app.include_router(stripe_api.router)