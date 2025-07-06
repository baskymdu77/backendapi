import os
import logging
import time
from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import JSONResponse
from typing import Optional, List
from serpapi import GoogleSearch
import json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("home_depot_api.log"),
    ]
)

logger = logging.getLogger("home_depot_api")

router = APIRouter(
    prefix="/home-depot",
    tags=["home-depot"],
    responses={404: {"description": "Not found"}},
)

def get_serpapi_key():
    """Get SerpAPI key from environment variables"""
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        logger.error("SERPAPI_API_KEY not found in environment variables")
        raise HTTPException(status_code=500, detail="SERPAPI_API_KEY not configured")
    return api_key

@router.get("/search")
async def search_home_depot(
    query: str = Query(..., description="Search query for Home Depot products"),
    location: Optional[str] = Query(None, description="Location for search results (e.g., 'Austin, Texas, United States')"),
    page: int = Query(1, description="Page number for pagination"),
    num_results: int = Query(10, description="Number of results per page (max 100)")
):
    """
    Search Home Depot products using SerpAPI
    
    - query: Search term for Home Depot products
    - location: Optional location to get region-specific results
    - page: Page number for pagination
    - num_results: Number of results per page (max 100)
    """
    start_time = time.time()
    request_id = f"hd-{int(start_time)}"
    logger.info(f"[{request_id}] Home Depot search request: query={query}, location={location}, page={page}")
    
    try:
        # Get SerpAPI key
        api_key = get_serpapi_key()
        
        # Prepare search parameters
        params = {
            "api_key": api_key,
            "engine": "home_depot",
            "q": query,
            "page": page,
            "num": min(num_results, 100)  # Limit to max 100 results
        }
        
        # Add location if provided
        if location:
            params["location"] = location
        
        logger.info(f"[{request_id}] Calling SerpAPI with parameters: {json.dumps({k: v for k, v in params.items() if k != 'api_key'})}")
        
        # Execute search
        api_start_time = time.time()
        search = GoogleSearch(params)
        results = search.get_dict()
        api_duration = time.time() - api_start_time
        logger.info(f"[{request_id}] SerpAPI call completed in {api_duration:.2f} seconds")
        
        # Return the response
        total_duration = time.time() - start_time
        logger.info(f"[{request_id}] Request completed successfully in {total_duration:.2f} seconds")
        
        return results
    
    except Exception as e:
        logger.error(f"[{request_id}] Error searching Home Depot: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/product/{product_id}")
async def get_home_depot_product(
    product_id: str,
    location: Optional[str] = Query(None, description="Location for product details (e.g., 'Austin, Texas, United States')")
):
    """
    Get detailed information about a specific Home Depot product
    
    - product_id: Home Depot product ID
    - location: Optional location to get region-specific product details
    """
    start_time = time.time()
    request_id = f"hdp-{int(start_time)}"
    logger.info(f"[{request_id}] Home Depot product request: product_id={product_id}, location={location}")
    
    try:
        # Get SerpAPI key
        api_key = get_serpapi_key()
        
        # Prepare search parameters
        params = {
            "api_key": api_key,
            "engine": "home_depot_product",
            "product_id": product_id
        }
        
        # Add location if provided
        if location:
            params["location"] = location
        
        logger.info(f"[{request_id}] Calling SerpAPI with parameters: {json.dumps({k: v for k, v in params.items() if k != 'api_key'})}")
        
        # Execute search
        api_start_time = time.time()
        search = GoogleSearch(params)
        results = search.get_dict()
        api_duration = time.time() - api_start_time
        logger.info(f"[{request_id}] SerpAPI call completed in {api_duration:.2f} seconds")
        
        # Return the response
        total_duration = time.time() - start_time
        logger.info(f"[{request_id}] Request completed successfully in {total_duration:.2f} seconds")
        
        return results
    
    except Exception as e:
        logger.error(f"[{request_id}] Error getting Home Depot product: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
