from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List, Dict, Any
import httpx
from bs4 import BeautifulSoup
import logging
import time
import uuid
import re
from urllib.parse import urlencode, quote_plus

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/home-depot-search", tags=["Home Depot Search OWN"])

class HomeDepotScraper:
    def __init__(self):
        self.base_url = "https://www.homedepot.com"
        self.search_url = "https://www.homedepot.com/b/N-5yc1v"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }

    async def search_products(self, query: str, location: str = "04401", page: int = 1, num_results: int = 24) -> Dict[str, Any]:
        """Search for products on Home Depot"""
        request_id = str(uuid.uuid4())[:8]
        start_time = time.time()
        
        logger.info(f"[{request_id}] Starting Home Depot search for query: '{query}', location: {location}, page: {page}")
        
        try:
            # Calculate pagination offset
            nao = (page - 1) * num_results
            
            # Build search URL with parameters
            search_params = {
                'Ntt': query,
                'Nao': str(nao),
                'Ntk': 'elasticplus',
                'NCNI': '5'
            }
            
            search_url = f"{self.search_url}/Ntk-elasticplus/Ntt-{quote_plus(query)}?Nao={nao}"
            
            # Set location cookie for store-specific results
            cookies = {
                'THD_CACHE_NAV_SESSION': '1',
                'THD_SESSION': f'zipCode={location}',
                'THD_PERSIST': f'C4%3D{location}%2BUS',
                'zipCode': location
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                logger.info(f"[{request_id}] Making request to: {search_url}")
                response = await client.get(search_url, headers=self.headers, cookies=cookies)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Extract products
                products = await self._extract_products(soup, request_id)
                
                # # Extract search information
                # search_info = await self._extract_search_info(soup, location)
                
                # # Extract filters
                # filters = await self._extract_filters(soup)
                
                # Build response in SerpAPI format
                elapsed_time = time.time() - start_time
                
                result = {
                    # "search_information": search_info,
                    "products": products,
                    # "filters": filters
                }
                
                logger.info(f"[{request_id}] Search completed successfully in {elapsed_time:.2f}s. Found {len(products)} products")
                return result
                
        except httpx.HTTPError as e:
            logger.error(f"[{request_id}] HTTP error during search: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch search results: {str(e)}")
        except Exception as e:
            logger.error(f"[{request_id}] Unexpected error during search: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

    async def _extract_products(self, soup: BeautifulSoup, request_id: str) -> List[Dict[str, Any]]:
        """Extract product information from search results"""
        products = []
        
        # Find product containers
        product_containers = soup.find_all('div', {'data-testid': 'product-pod'}) or \
                           soup.find_all('div', class_=re.compile(r'product-pod|plp-pod'))
        
        if not product_containers:
            # Try alternative selectors
            product_containers = soup.find_all('div', class_=re.compile(r'browse-search__pod'))
        
        logger.info(f"[{request_id}] Found {len(product_containers)} product containers")
        
        for idx, container in enumerate(product_containers[:1]):  # Limit to 24 products
            try:
                product = await self._extract_single_product(container, idx + 1)
                if product:
                    products.append(product)
            except Exception as e:
                logger.warning(f"[{request_id}] Error extracting product {idx + 1}: {str(e)}")
                continue
        
        return products

    async def _extract_single_product(self, container: BeautifulSoup, position: int) -> Optional[Dict[str, Any]]:
        """Extract information for a single product"""
        try:
            # Extract product ID from link
            product_link = container.find('a', href=True)
            if not product_link:
                return None
            
            href = product_link['href']
            product_id_match = re.search(r'/p/[^/]+/(\d+)', href)
            product_id = product_id_match.group(1) if product_id_match else None
            
            if not product_id:
                return None
            
            # Extract title
            title_elem = container.find('span', {'data-testid': 'product-title'}) or \
                        container.find('a', class_=re.compile(r'product-title'))
            title = title_elem.get_text(strip=True) if title_elem else "N/A"
            
            # Extract price
            price_elem = container.find('span', {'data-testid': 'price'}) or \
                        container.find('span', class_=re.compile(r'price'))
            price_text = price_elem.get_text(strip=True) if price_elem else "0"
            price = self._parse_price(price_text)
            
            # Extract image thumbnails
            img_elem = container.find('img')
            thumbnails = []
            if img_elem and img_elem.get('src'):
                base_url = img_elem['src']
                # Generate different sizes like in the example
                sizes = ['65', '100', '145', '300', '400', '600', '1000']
                thumbnail_set = []
                for size in sizes:
                    thumbnail_url = base_url.replace('_65.', f'_{size}.')
                    thumbnail_set.append(thumbnail_url)
                thumbnails.append(thumbnail_set)
            
            # Extract brand
            brand_elem = container.find('span', {'data-testid': 'product-brand'}) or \
                        container.find('span', class_=re.compile(r'brand'))
            brand = brand_elem.get_text(strip=True) if brand_elem else "N/A"
            
            # Extract model number
            model_elem = container.find('span', {'data-testid': 'product-model'}) or \
                        container.find('span', class_=re.compile(r'model'))
            model_number = model_elem.get_text(strip=True) if model_elem else "N/A"
            
            # Extract rating and reviews
            rating_elem = container.find('span', class_=re.compile(r'rating'))
            rating = 0.0
            reviews = 0
            
            if rating_elem:
                rating_text = rating_elem.get_text(strip=True)
                rating_match = re.search(r'(\d+\.?\d*)', rating_text)
                if rating_match:
                    rating = float(rating_match.group(1))
            
            reviews_elem = container.find('span', class_=re.compile(r'review'))
            if reviews_elem:
                reviews_text = reviews_elem.get_text(strip=True)
                reviews_match = re.search(r'(\d+)', reviews_text)
                if reviews_match:
                    reviews = int(reviews_match.group(1))
            
            # Build full product URL
            full_link = href if href.startswith('http') else f"https://www.homedepot.com{href}"
            
            # Build product data
            product = {
                "position": position,
                "product_id": product_id,
                "title": title,
                "thumbnails": thumbnails,
                "link": full_link,
                "model_number": model_number,
                "brand": brand,
                "collection": "https://www.homedepot.com",
                "rating": rating,
                "reviews": reviews,
                "price": price
            }
            
            # Add optional fields if available
            if rating >= 4.5:
                product["badges"] = ["top rated"]
            elif rating >= 4.0:
                product["badges"] = ["highly rated"]
            
            # Add delivery and pickup info (mock data based on typical HD behavior)
            product["delivery"] = {
                "free": price > 45,
                "free_delivery_threshold": price <= 45
            }
            
            product["pickup"] = {
                "free_ship_to_store": True
            }
            
            return product
            
        except Exception as e:
            logger.warning(f"Error extracting single product: {str(e)}")
            return None

    async def _extract_search_info(self, soup: BeautifulSoup, location: str) -> Dict[str, Any]:
        """Extract search information"""
        # Try to find total results
        results_elem = soup.find('span', class_=re.compile(r'results-count|total-results'))
        total_results = 0
        
        if results_elem:
            results_text = results_elem.get_text(strip=True)
            results_match = re.search(r'(\d+(?:,\d+)*)', results_text)
            if results_match:
                total_results = int(results_match.group(1).replace(',', ''))
        
        # Mock store information based on location
        store_info = self._get_store_info(location)
        
        return {
            "results_state": "Results for exact spelling",
            "total_results": total_results,
            "store_id": store_info["store_id"],
            "store_name": store_info["store_name"]
        }

    async def _extract_filters(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """Extract filter information"""
        filters = []
        
        # Mock common filters based on typical Home Depot structure
        mock_filters = [
            {
                "key": "Review Rating",
                "value": [
                    {"name": "4 & Up", "count": "5731", "value": "bwo5o", "link": "https://www.homedepot.com/b/Highly-Rated/"},
                    {"name": "3 & Up", "count": "6581", "value": "bwo5n", "link": "https://www.homedepot.com/b/"},
                ]
            },
            {
                "key": "Brand",
                "value": [
                    {"name": "American Craftsman", "count": "163", "value": "aso", "link": "https://www.homedepot.com/b/American-Craftsman/"},
                    {"name": "TAFCO WINDOWS", "count": "113", "value": "53q", "link": "https://www.homedepot.com/b/TAFCO-WINDOWS/"},
                    {"name": "JELD-WEN", "count": "976", "value": "2he", "link": "https://www.homedepot.com/b/JELD-WEN/"},
                ]
            },
            {
                "key": "Price",
                "value": [
                    {"name": "$0 - $50", "count": "422", "value": "12kx", "link": "https://www.homedepot.com/b/"},
                    {"name": "$50 - $100", "count": "586", "value": "12l2", "link": "https://www.homedepot.com/b/"},
                    {"name": "$100 - $200", "count": "905", "value": "12l4", "link": "https://www.homedepot.com/b/"},
                ]
            }
        ]
        
        return mock_filters

    def _parse_price(self, price_text: str) -> float:
        """Parse price from text"""
        if not price_text:
            return 0.0
        
        # Remove currency symbols and extract number
        price_match = re.search(r'(\d+(?:\.\d{2})?)', price_text.replace(',', ''))
        if price_match:
            return float(price_match.group(1))
        return 0.0

    def _get_store_info(self, zip_code: str) -> Dict[str, str]:
        """Get store information based on ZIP code"""
        # Mock store data - in real implementation, this would lookup actual stores
        store_mapping = {
            "04401": {"store_id": "2414", "store_name": "Bangor"},
            "10001": {"store_id": "1234", "store_name": "Manhattan"},
            "90210": {"store_id": "5678", "store_name": "Beverly Hills"},
            "60601": {"store_id": "9012", "store_name": "Chicago"},
        }
        
        return store_mapping.get(zip_code, {"store_id": "2414", "store_name": "Bangor"})

# Initialize scraper
scraper = HomeDepotScraper()

@router.get("/search")
async def search_home_depot_products(
    query: str = Query(..., description="Search query for products"),
    location: Optional[str] = Query("04401", description="ZIP code for location-based results"),
    page: Optional[int] = Query(1, ge=1, description="Page number for pagination"),
    num_results: Optional[int] = Query(24, ge=1, le=100, description="Number of results per page")
):
    """
    Search for products on Home Depot with the exact format as SerpAPI
    
    Returns structured product data including:
    - Product details (title, price, brand, model)
    - Images and thumbnails
    - Ratings and reviews
    - Availability and delivery options
    - Search metadata and filters
    """
    try:
        results = await scraper.search_products(
            query=query,
            location=location,
            page=page,
            num_results=num_results
        )
        return results
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in search endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error during search")

@router.get("/product/{product_id}")
async def get_product_details(
    product_id: str,
    location: Optional[str] = Query("04401", description="ZIP code for location-based pricing and availability")
):
    """
    Get detailed information for a specific Home Depot product
    """
    request_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    
    logger.info(f"[{request_id}] Getting product details for ID: {product_id}")
    
    try:
        # Build product URL
        product_url = f"https://www.homedepot.com/p/{product_id}"
        
        cookies = {
            'THD_CACHE_NAV_SESSION': '1',
            'THD_SESSION': f'zipCode={location}',
            'THD_PERSIST': f'C4%3D{location}%2BUS',
            'zipCode': location
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(product_url, headers=scraper.headers, cookies=cookies)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract detailed product information
            product_details = await _extract_product_details(soup, product_id, request_id)
            
            elapsed_time = time.time() - start_time
            logger.info(f"[{request_id}] Product details retrieved in {elapsed_time:.2f}s")
            
            return product_details
            
    except httpx.HTTPError as e:
        logger.error(f"[{request_id}] HTTP error getting product details: {str(e)}")
        raise HTTPException(status_code=404, detail=f"Product not found: {product_id}")
    except Exception as e:
        logger.error(f"[{request_id}] Error getting product details: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve product details")

async def _extract_product_details(soup: BeautifulSoup, product_id: str, request_id: str) -> Dict[str, Any]:
    """Extract detailed product information from product page"""
    try:
        # Extract title
        title_elem = soup.find('h1', {'data-testid': 'product-title'}) or soup.find('h1')
        title = title_elem.get_text(strip=True) if title_elem else "N/A"
        
        # Extract price
        price_elem = soup.find('span', {'data-testid': 'price'}) or \
                    soup.find('span', class_=re.compile(r'price'))
        price_text = price_elem.get_text(strip=True) if price_elem else "0"
        price = scraper._parse_price(price_text)
        
        # Extract brand
        brand_elem = soup.find('span', {'data-testid': 'product-brand'})
        brand = brand_elem.get_text(strip=True) if brand_elem else "N/A"
        
        # Extract model number
        model_elem = soup.find('span', {'data-testid': 'product-model'})
        model_number = model_elem.get_text(strip=True) if model_elem else "N/A"
        
        # Extract description
        desc_elem = soup.find('div', {'data-testid': 'product-description'})
        description = desc_elem.get_text(strip=True) if desc_elem else ""
        
        # Extract specifications
        specs = {}
        spec_section = soup.find('div', class_=re.compile(r'specifications|product-details'))
        if spec_section:
            spec_items = spec_section.find_all('div', class_=re.compile(r'spec-item'))
            for item in spec_items:
                key_elem = item.find('span', class_=re.compile(r'spec-key'))
                value_elem = item.find('span', class_=re.compile(r'spec-value'))
                if key_elem and value_elem:
                    specs[key_elem.get_text(strip=True)] = value_elem.get_text(strip=True)
        
        return {
            "product_id": product_id,
            "title": title,
            "price": price,
            "brand": brand,
            "model_number": model_number,
            "description": description,
            "specifications": specs,
            "availability": {
                "in_stock": True,  # Mock data
                "store_pickup": True,
                "delivery_available": True
            }
        }
        
    except Exception as e:
        logger.error(f"[{request_id}] Error extracting product details: {str(e)}")
        return {
            "product_id": product_id,
            "title": "Product details unavailable",
            "error": str(e)
        }
