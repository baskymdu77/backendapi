import os
import logging
import time
import datetime
import re
import hashlib
from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import JSONResponse
from typing import Optional, List, Dict, Any, Union
from serpapi import GoogleSearch
import json
from app.services.firebase_admin import get_firestore

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
    prefix="/home-depot-api",
    tags=["home-depot-api"],
    responses={404: {"description": "Not found"}},
)

def sanitize_cache_key(key: str) -> str:
    """
    Sanitize a cache key to ensure it's valid for Firestore document IDs.
    Firestore document IDs must not contain: /, ., .., *, [, ], ~, `, or any Unicode character < 32 or >= 127
    
    Args:
        key: The original cache key string
        
    Returns:
        A sanitized version of the key that's safe for Firestore
    """
    # If key is too long or contains problematic characters, hash it
    if len(key) > 1500 or re.search(r'[/\.\*\[\]~`\x00-\x1F\x7F-\xFF]', key):
        # Create a hash of the key
        return hashlib.md5(key.encode('utf-8')).hexdigest()
    
    # Replace problematic characters
    sanitized = re.sub(r'[/\.\*\[\]~`\x00-\x1F\x7F-\xFF]', '_', key)
    return sanitized


def sanitize_for_firestore(data: Any, max_depth: int = 10, current_depth: int = 0) -> Any:
    """
    Sanitize data to ensure it's compatible with Firestore storage.
    Firestore doesn't support certain data types like functions, custom objects, etc.
    
    Args:
        data: The data to sanitize
        max_depth: Maximum depth for nested objects to prevent infinite recursion
        current_depth: Current recursion depth
        
    Returns:
        A sanitized version of the data that's safe for Firestore
    """
    # Prevent infinite recursion
    if current_depth > max_depth:
        return "[Max depth exceeded]"
    
    if data is None or isinstance(data, (bool, int, float, str)):
        # Basic types are fine
        return data
    
    elif isinstance(data, (list, tuple)):
        # Process each item in lists/tuples
        # Special handling for deeply nested arrays (like thumbnails in Home Depot API)
        if current_depth >= 3 and len(data) > 0 and isinstance(data[0], (list, tuple, dict)):
            # Convert deeply nested structures to JSON string
            try:
                return json.dumps(data)
            except Exception:
                return str(data)
        return [sanitize_for_firestore(item, max_depth, current_depth + 1) for item in data]
    
    elif isinstance(data, dict):
        # Process each key-value pair in dictionaries
        result = {}
        for key, value in data.items():
            # Firestore doesn't allow '.' in field names
            safe_key = key.replace('.', '_')
            # Skip empty keys
            if safe_key:
                # Special handling for known problematic fields in Home Depot API
                if safe_key in ['thumbnails', 'variants', 'filters'] and isinstance(value, (list, dict)):
                    try:
                        # Store these as JSON strings instead of nested objects
                        result[safe_key] = json.dumps(value)
                    except Exception:
                        result[safe_key] = str(value)
                else:
                    result[safe_key] = sanitize_for_firestore(value, max_depth, current_depth + 1)
        return result
    
    else:
        # Convert anything else to string representation
        try:
            return str(data)
        except Exception:
            return "[Unsupported data type]"

def get_serpapi_key():
    """Get SerpAPI key from environment variables"""
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        logger.error("SERPAPI_API_KEY not found in environment variables")
        raise HTTPException(status_code=500, detail="SERPAPI_API_KEY not configured")
    return api_key


def get_cache_duration(days=None):
    """
    Get cache duration in seconds from environment variables or use provided value
    
    Args:
        days: Optional number of days to override the default
        
    Returns:
        Cache duration in seconds
    """
    # If days parameter is provided, use that
    if days is not None:
        return days * 86400  # Convert days to seconds
        
    # Get from environment variable or use default (1 day)
    try:
        env_days = os.getenv("HOME_DEPOT_CACHE_DAYS")
        if env_days:
            return int(env_days) * 86400  # Convert days to seconds
    except (ValueError, TypeError):
        logger.warning("Invalid HOME_DEPOT_CACHE_DAYS value, using default")
        
    # Default: 1 day
    return 86400


def simplify_response(results, is_cached=False):
    """Simplify API response to return only products and cache status"""
    products = results.get('products', [])
    return {
        "products": products,
        "is_cached": is_cached
    }

@router.get("/search")
async def search_home_depot(
    query: str = Query(..., description="Search query for Home Depot products"),
    location: Optional[str] = Query(None, description="Location for search results (e.g., 'Austin, Texas, United States')"),
    page: int = Query(1, description="Page number for pagination"),
    num_results: int = Query(100, description="Number of results per page (max 100)"),
    cache_days: Optional[int] = Query(None, description="Cache duration in days (overrides default)"),
    force_refresh: bool = Query(False, description="Force refresh data from SerpAPI instead of using cache")
):
    """
    Search Home Depot products using SerpAPI with Firebase caching
    
    - query: Search term for Home Depot products
    - location: Optional location to get region-specific results
    - page: Page number for pagination
    - num_results: Number of results per page (max 100)
    - cache_days: Optional cache duration in days (overrides default from HOME_DEPOT_CACHE_DAYS)
    - force_refresh: If True, bypass cache and fetch fresh data from SerpAPI
    """
    start_time = time.time()
    request_id = f"hd-{int(start_time)}"
    logger.info(f"[{request_id}] Home Depot search request: query={query}, location={location}, page={page}")
    
    try:
        # Create a cache key based on search parameters
        cache_params = {
            "query": query,
            "page": page,
            "num_results": num_results
        }
        if location:
            cache_params["location"] = location
            
        # Create cache key and sanitize it
        cache_key = f"{query}_{page}_{num_results}"
        if location:
            cache_key += f"_{location}"
        cache_key = sanitize_cache_key(cache_key)
        
        # Get Firestore client
        db = get_firestore()
        cache_collection = db.collection('home_depot_cache')
        
        # Check if we have a cached version (unless force_refresh is True)
        if not force_refresh:
            cache_doc = cache_collection.document(cache_key).get()
            
            if cache_doc.exists:
                cache_data = cache_doc.to_dict()
                cache_timestamp = cache_data.get('timestamp')
                current_time = datetime.datetime.now()
                
                # Convert timestamp to datetime if it's a Firestore timestamp
                if hasattr(cache_timestamp, 'timestamp'):
                    cache_datetime = datetime.datetime.fromtimestamp(cache_timestamp.timestamp())
                else:
                    cache_datetime = datetime.datetime.fromisoformat(cache_timestamp)
                    
                # Get cache duration (in seconds)
                cache_duration = get_cache_duration(cache_days)
                
                # Check if cache is still valid based on configured duration
                cache_age = current_time - cache_datetime
                if cache_age.total_seconds() < cache_duration:
                    cache_age_hours = cache_age.total_seconds() / 3600
                    logger.info(f"[{request_id}] Returning cached search results from {cache_age_hours:.1f} hours ago (max age: {cache_duration/3600:.1f} hours)")
                    
                    # Ensure we return the data in the original format
                    cached_results = cache_data['results']
                    
                    # Convert any JSON strings back to objects for consistent response format
                    if 'products' in cached_results and isinstance(cached_results['products'], list):
                        for product in cached_results['products']:
                            if 'thumbnails' in product and isinstance(product['thumbnails'], str):
                                try:
                                    product['thumbnails'] = json.loads(product['thumbnails'])
                                except Exception:
                                    pass  # Keep as string if can't parse
                                    
                            if 'variants' in product and isinstance(product['variants'], str):
                                try:
                                    product['variants'] = json.loads(product['variants'])
                                except Exception:
                                    pass  # Keep as string if can't parse
                    
                    if 'filters' in cached_results and isinstance(cached_results['filters'], str):
                        try:
                            cached_results['filters'] = json.loads(cached_results['filters'])
                        except Exception:
                            pass  # Keep as string if can't parse
                    
                    # Return simplified response with just products and cache status
                    return simplify_response(cached_results, is_cached=True)
                else:
                    cache_age_hours = cache_age.total_seconds() / 3600
                    logger.info(f"[{request_id}] Search cache expired ({cache_age_hours:.1f} hours old, max age: {cache_duration/3600:.1f} hours)")
            else:
                logger.info(f"[{request_id}] No cache found for search: {query}, page: {page}")
        else:
            logger.info(f"[{request_id}] Force refresh requested, bypassing cache for search: {query}, page: {page}")
        
        # If no valid cache, call SerpAPI
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
        
        # Store results in Firebase cache - sanitize data first
        try:
            # First, identify and handle known problematic fields
            if 'products' in results and isinstance(results['products'], list):
                for product in results['products']:
                    # Convert thumbnails to JSON strings directly
                    if 'thumbnails' in product and isinstance(product['thumbnails'], list):
                        product['thumbnails'] = json.dumps(product['thumbnails'])
                    # Convert variants to JSON strings directly
                    if 'variants' in product and isinstance(product['variants'], list):
                        product['variants'] = json.dumps(product['variants'])
            
            # Handle filters separately if present
            if 'filters' in results and isinstance(results['filters'], list):
                results['filters'] = json.dumps(results['filters'])
                
            # Now sanitize the entire structure
            sanitized_results = sanitize_for_firestore(results)
            
            cache_data = {
                'results': sanitized_results,
                'timestamp': datetime.datetime.now().isoformat(),
                'params': cache_params
            }
            
            try:
                cache_collection.document(cache_key).set(cache_data)
                logger.info(f"[{request_id}] Stored results in Firebase cache with key: {cache_key}")
            except Exception as e:
                logger.error(f"[{request_id}] Failed to store in Firebase: {str(e)}")
                # Log more details about the error
                if hasattr(e, '__dict__'):
                    logger.error(f"[{request_id}] Error details: {e.__dict__}")
        except Exception as e:
            logger.error(f"[{request_id}] Error preparing data for Firebase: {str(e)}")
            # Continue without caching if there's an error
        
        # Return the response
        total_duration = time.time() - start_time
        logger.info(f"[{request_id}] Request completed successfully in {total_duration:.2f} seconds")
        
        # Return simplified response with just products and cache status
        return simplify_response(results, is_cached=False)
    
    except Exception as e:
        logger.error(f"[{request_id}] Error searching Home Depot: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/product/{product_id}")
async def get_home_depot_product(
    product_id: str,
    location: Optional[str] = Query(None, description="Location for product details (e.g., 'Austin, Texas, United States')"),
    cache_days: Optional[int] = Query(None, description="Cache duration in days (overrides default)"),
    force_refresh: bool = Query(False, description="Force refresh data from SerpAPI instead of using cache")
):
    """
    Get detailed information about a specific Home Depot product with Firebase caching
    
    - product_id: Home Depot product ID
    - location: Optional location to get region-specific product details
    - cache_days: Optional cache duration in days (overrides default from HOME_DEPOT_CACHE_DAYS)
    - force_refresh: If True, bypass cache and fetch fresh data from SerpAPI
    """
    start_time = time.time()
    request_id = f"hdp-{int(start_time)}"
    logger.info(f"[{request_id}] Home Depot product request: product_id={product_id}, location={location}")
    
    try:
        # Create a cache key based on product ID and location
        cache_params = {
            "product_id": product_id
        }
        if location:
            cache_params["location"] = location
            
        # Create cache key and sanitize it
        cache_key = f"product_{product_id}"
        if location:
            cache_key += f"_{location}"
        cache_key = sanitize_cache_key(cache_key)
        
        # Get Firestore client
        db = get_firestore()
        cache_collection = db.collection('home_depot_product_cache')
        
        # Check if we have a cached version (unless force_refresh is True)
        if not force_refresh:
            cache_doc = cache_collection.document(cache_key).get()
            
            if cache_doc.exists:
                cache_data = cache_doc.to_dict()
                cache_timestamp = cache_data.get('timestamp')
                current_time = datetime.datetime.now()
                
                # Convert timestamp to datetime if it's a Firestore timestamp
                if hasattr(cache_timestamp, 'timestamp'):
                    cache_datetime = datetime.datetime.fromtimestamp(cache_timestamp.timestamp())
                else:
                    cache_datetime = datetime.datetime.fromisoformat(cache_timestamp)
                    
                # Get cache duration (in seconds)
                cache_duration = get_cache_duration(cache_days)
                
                # Check if cache is still valid based on configured duration
                cache_age = current_time - cache_datetime
                if cache_age.total_seconds() < cache_duration:
                    cache_age_hours = cache_age.total_seconds() / 3600
                    logger.info(f"[{request_id}] Returning cached product details from {cache_age_hours:.1f} hours ago (max age: {cache_duration/3600:.1f} hours)")
                    
                    # Ensure we return the data in the original format
                    cached_results = cache_data['results']
                    
                    # Convert any JSON strings back to objects for consistent response format
                    if 'thumbnails' in cached_results and isinstance(cached_results['thumbnails'], str):
                        try:
                            cached_results['thumbnails'] = json.loads(cached_results['thumbnails'])
                        except Exception:
                            pass  # Keep as string if can't parse
                            
                    if 'variants' in cached_results and isinstance(cached_results['variants'], str):
                        try:
                            cached_results['variants'] = json.loads(cached_results['variants'])
                        except Exception:
                            pass  # Keep as string if can't parse
                            
                    if 'specifications' in cached_results and isinstance(cached_results['specifications'], str):
                        try:
                            cached_results['specifications'] = json.loads(cached_results['specifications'])
                        except Exception:
                            pass  # Keep as string if can't parse
                            
                    if 'images' in cached_results and isinstance(cached_results['images'], str):
                        try:
                            cached_results['images'] = json.loads(cached_results['images'])
                        except Exception:
                            pass  # Keep as string if can't parse
                    
                    # Return product details with cache status
                    return {
                        "product": cached_results,
                        "is_cached": True
                    }
                else:
                    cache_age_hours = cache_age.total_seconds() / 3600
                    logger.info(f"[{request_id}] Product cache expired ({cache_age_hours:.1f} hours old, max age: {cache_duration/3600:.1f} hours)")
            else:
                logger.info(f"[{request_id}] No cache found for product: {product_id}")
        else:
            logger.info(f"[{request_id}] Force refresh requested, bypassing cache for product: {product_id}")
        
        # If no valid cache, call SerpAPI
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
        
        # Store results in Firebase cache - sanitize data first
        try:
            # First, identify and handle known problematic fields
            # Handle thumbnails, variants, and other complex fields
            if 'thumbnails' in results and isinstance(results['thumbnails'], list):
                results['thumbnails'] = json.dumps(results['thumbnails'])
                
            if 'variants' in results and isinstance(results['variants'], list):
                results['variants'] = json.dumps(results['variants'])
                
            if 'specifications' in results and isinstance(results['specifications'], list):
                results['specifications'] = json.dumps(results['specifications'])
                
            if 'images' in results and isinstance(results['images'], list):
                results['images'] = json.dumps(results['images'])
                
            # Now sanitize the entire structure
            sanitized_results = sanitize_for_firestore(results)
            
            cache_data = {
                'results': sanitized_results,
                'timestamp': datetime.datetime.now().isoformat(),
                'params': cache_params
            }
            
            try:
                cache_collection.document(cache_key).set(cache_data)
                logger.info(f"[{request_id}] Stored product details in Firebase cache with key: {cache_key}")
            except Exception as e:
                logger.error(f"[{request_id}] Failed to store product details in Firebase: {str(e)}")
                # Log more details about the error
                if hasattr(e, '__dict__'):
                    logger.error(f"[{request_id}] Error details: {e.__dict__}")
        except Exception as e:
            logger.error(f"[{request_id}] Error preparing product data for Firebase: {str(e)}")
            # Continue without caching if there's an error
        
        # Return the response
        total_duration = time.time() - start_time
        logger.info(f"[{request_id}] Request completed successfully in {total_duration:.2f} seconds")
        
        # Return product details with cache status
        return {
            "product": results,
            "is_cached": False
        }
    
    except Exception as e:
        logger.error(f"[{request_id}] Error getting Home Depot product: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
