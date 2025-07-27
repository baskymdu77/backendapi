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
            # Debug: Log the container HTML structure for the first product
            if position == 1:
                logger.info(f"First product container HTML snippet: {str(container)[:500]}...")
            # Extract product link and ID - updated selectors
            link_selectors = [
                'a[data-testid="product-link"]',
                'a[data-automation-id="product-link"]', 
                'a[href*="/p/"]',
                'a[href*="product"]',
                'a.sui-btn-text',  # Based on Home Depot's button classes
                'h3 a',
                'h2 a', 
                '.product-title a',
                'a:first-child'
            ]
            
            product_link = None
            for selector in link_selectors:
                product_link = container.select_one(selector)
                if product_link and product_link.get('href'):
                    break
            
            if not product_link:
                return None
            
            href = product_link['href']
            product_id_match = re.search(r'/p/[^/]+/(\d+)', href)
            product_id = product_id_match.group(1) if product_id_match else None
            
            if not product_id:
                return None
            
            # Extract title - updated selectors based on Home Depot structure
            title_selectors = [
                'h3[data-testid="product-title"]',
                'h2[data-testid="product-title"]',
                'span[data-testid="product-title"]',
                'a[data-testid="product-title"]',
                'h3.sui-h6-bold',  # Home Depot uses these heading classes
                'h2.sui-h5-bold',
                'h3.sui-text-base',
                '.product-title',
                'h3 a',
                'h2 a',
                'a[data-testid="product-link"]',
                '.product-name',
                'span.sui-line-clamp-2',  # Product titles often use line clamping
                'a[data-automation-id="product-title"]',
                '.product-pod__title',
                '.browse-search__pod__title'
            ]
            
            title = "Product Title Not Found"
            for selector in title_selectors:
                title_elem = container.select_one(selector)
                if title_elem:
                    title_text = title_elem.get_text(strip=True)
                    if title_text and len(title_text) > 3:  # Ensure it's a meaningful title
                        title = title_text
                        break
            
            # If still no title, try getting it from the product link
            if title == "Product Title Not Found" and product_link:
                link_text = product_link.get_text(strip=True)
                if link_text and len(link_text) > 3:
                    title = link_text
            
            # Extract price - improved logic for variable pricing
            price_selectors = [
                "#standard-price",
                'span[data-testid="price"]',
                'span[data-automation-id="product-price"]',
                'div[data-testid="price-range"]',  # For price ranges
                'span.sui-text-xl.sui-font-bold',  # Common price styling
                'span.sui-text-lg.sui-font-bold',
                'span.sui-text-2xl.sui-font-bold',  # Larger price text
                'span.price',
                'span.price-format',
                '.price__dollars',
                '.price-current',
                '.price-display',
                'span[aria-label*="dollar"]',
                '.price-range__start',
                '.price-from',
                '.starting-at'
            ]
            
            price = 0.0
            price_text = ""
            price_range = None
            
            # First try to find specific price elements
            for selector in price_selectors:
                try:
                    price_elem = container.select_one(selector)
                    if price_elem:
                        price_text = price_elem.get_text(strip=True)
                        print(f"Price text: {price_text}")
                        # Check for price ranges (e.g., "$10.99 - $25.99" or "Starting at $15.99")
                        if 'starting' in price_text.lower() or 'from' in price_text.lower():
                            # Extract the starting price
                            price_match = re.search(r'\$([\d,]+(?:\.\d{2})?)', price_text)
                            if price_match:
                                price = self._parse_price(price_match.group())
                                price_range = f"Starting at {price_match.group()}"
                                break
                        elif '-' in price_text and '$' in price_text:
                            # Handle price ranges like "$10.99 - $25.99"
                            prices = re.findall(r'\$([\d,]+(?:\.\d{2})?)', price_text)
                            if len(prices) >= 2:
                                price = self._parse_price(f"${prices[0]}")
                                price_range = price_text
                                break
                            elif len(prices) == 1:
                                price = self._parse_price(f"${prices[0]}")
                                break
                        else:
                            # Regular single price
                            parsed_price = self._parse_price(price_text)
                            if parsed_price > 0:
                                price = parsed_price
                                break
                except Exception as e:
                    continue
            
            # If still no price found, search for any dollar amounts in the container
            if price == 0.0:
                # Look for price patterns in all text
                all_text = container.get_text()
                
                # Try different price patterns
                price_patterns = [
                    r'\$([\d,]+\.\d{2})',  # $123.45
                    r'\$([\d,]+)',          # $123
                    r'([\d,]+\.\d{2})\s*dollars?',  # 123.45 dollars
                    r'([\d,]+)\s*dollars?'           # 123 dollars
                ]
                
                for pattern in price_patterns:
                    matches = re.findall(pattern, all_text, re.IGNORECASE)
                    if matches:
                        for match in matches:
                            parsed_price = self._parse_price(f"${match}")
                            if parsed_price > 0:
                                price = parsed_price
                                break
                    if price > 0:
                        break
                
                # Last resort: find any dollar sign followed by numbers
                if price == 0.0:
                    dollar_matches = re.findall(r'\$[\d,]+(?:\.\d{2})?', all_text)
                    if dollar_matches:
                        # Take the first reasonable price (not too small, not too large)
                        for match in dollar_matches:
                            parsed_price = self._parse_price(match)
                            if 0.01 <= parsed_price <= 50000:  # Reasonable price range
                                price = parsed_price
                                break
            print(f"Price: {price}")
            # Extract thumbnail image
            img_selectors = [
                'img[data-testid="product-image"]',
                'img[data-automation-id="product-image"]',
                'img[data-testid="product-pod-image"]',
                '.product-image img',
                '.product-thumbnail img',
                'img[alt*="product"]',
                'img[src*="product"]',
                'picture img',
                'img.sui-object-contain',  # Home Depot image styling
                'img.sui-object-cover',
                'img:first-child'
            ]
            
            thumbnails = []
            for selector in img_selectors:
                img_elem = container.select_one(selector)
                if img_elem and img_elem.get('src'):
                    thumbnail_url = img_elem['src']
                    if not thumbnail_url.startswith('http'):
                        thumbnail_url = f"https://www.homedepot.com{thumbnail_url}"
                    
                    # Create thumbnail set
                    thumbnail_set = []
                    if thumbnail_url:
                        thumbnail_set.append(thumbnail_url)
                    thumbnails.append(thumbnail_set)
                    break
            
            # Extract brand - improved extraction with multiple strategies
            brand_selectors = [
                'span[data-testid="attribute-brandname-inline"]'
            ]
            
            brand = "Unknown Brand"
            
            # Try specific brand selectors first
            for selector in brand_selectors:
                brand_elem = container.select_one(selector)
                if brand_elem:
                    brand = brand_elem.get_text(strip=True)
                    break
            
        
            
            model_number = "N/A"

            for div in container.select('div[data-testid="pod-section"] div.sui-flex'):
                text = div.get_text(strip=True)
                if "Model#" in text:
                    model_number = text.split("Model#")[-1].strip()
                    break
          
            # Extract rating and reviews - updated selectors
            rating_selectors = [
                'span[data-testid="rating"]',
                'span[data-automation-id="rating"]',
                'div[data-testid="rating-stars"]',
                '.rating',
                '.stars',
                '.star-rating',
                'span[aria-label*="star"]',
                'span[aria-label*="rating"]',
                '.review-stars',
                '.product-rating',
                'div.sui-flex[aria-label*="star"]'  # Home Depot rating containers
            ]
            
            rating = 0.0
            for selector in rating_selectors:
                rating_elem = container.select_one(selector)
                if rating_elem:
                    # Try to get rating from aria-label first
                    aria_label = rating_elem.get('aria-label', '')
                    if aria_label:
                        rating_match = re.search(r'(\d+\.?\d*)', aria_label)
                        if rating_match:
                            rating = float(rating_match.group(1))
                            if position == 1:
                                logger.info(f"Found rating {rating} from aria-label: {aria_label}")
                            break
                    
                    # Try to get rating from text content
                    rating_text = rating_elem.get_text(strip=True)
                    if rating_text:
                        rating_match = re.search(r'(\d+\.?\d*)', rating_text)
                        if rating_match:
                            rating = float(rating_match.group(1))
                            break
            
            # Extract reviews count
            review_selectors = [
                'span[data-testid="reviews"]',
                'span[data-automation-id="reviews"]',
                'span[data-testid="review-count"]',
                'a[data-testid="reviews-link"]',
                '.review-count',
                '.reviews-count',
                '.product-reviews',
                'span:contains("review")',
                'a:contains("review")',
                'span.sui-text-sm:contains("review")',  # Reviews often in small text
                'a.sui-btn-text:contains("review")'  # Review links as text buttons
            ]
            
            reviews = 0
            for selector in review_selectors:
                if ':contains(' in selector:
                    # Handle special :contains selector
                    review_elems = container.find_all(['span', 'a'])
                    for elem in review_elems:
                        text = elem.get_text(strip=True).lower()
                        if 'review' in text:
                            reviews_match = re.search(r'(\d+)', text)
                            if reviews_match:
                                reviews = int(reviews_match.group(1))
                                break
                    if reviews > 0:
                        break
                else:
                    reviews_elem = container.select_one(selector)
                    if reviews_elem:
                        reviews_text = reviews_elem.get_text(strip=True)
                        reviews_match = re.search(r'(\d+)', reviews_text)
                        if reviews_match:
                            reviews = int(reviews_match.group(1))
                            break
            
            # Build full product URL
            full_link = href if href.startswith('http') else f"https://www.homedepot.com{href}"
            
            # Debug logging for first product
            if position == 1:
                logger.info(f"Product {position}: title='{title}', price={price}, brand='{brand}', model='{model_number}'")
                logger.info(f"Product {position}: rating={rating}, reviews={reviews}, thumbnails_count={len(thumbnails)}")
                logger.info(f"Product {position}: product_id='{product_id}', link='{href}'")
                if price_range:
                    logger.info(f"Product {position}: price_range='{price_range}'")
            
            # Build product data
            product = {
                "position": position,
                "product_id": product_id,
                "title": title,
                "link": full_link,
                "brand": brand,
                "model_number": model_number,
                "price": price,
                "rating": rating,
                "reviews": reviews,
                "html": str(container)
            }
            
            # Add price range info if available
            if price_range:
                product["price_range"] = price_range
            
            # Add thumbnails if available
            if thumbnails:
                product["thumbnails"] = thumbnails
            
            # Add optional fields if available
            if rating >= 4.5:
                product["badges"] = ["top rated"]
            elif rating >= 4.0:
                product["badges"] = ["highly rated"]
            
            # Add availability info if price was found
            if price > 0:
                product["delivery"] = {
                    "available": True,
                    "free_shipping": price > 45
                }
                product["pickup"] = {
                    "available": True
                }
            
            return product
            
        except Exception as e:
            logger.warning(f"Error extracting single product: {str(e)}")
            return None

    def _parse_price(self, price_text: str) -> float:
        """Parse price from text with improved regex"""
        if not price_text:
            return 0.0
        
        # Clean the text and extract price
        cleaned_text = price_text.replace(',', '').replace('$', '').strip()
        
        # Remove common non-price text
        cleaned_text = re.sub(r'(starting at|from|each|per|was|now|save|off)', '', cleaned_text, flags=re.IGNORECASE)
        cleaned_text = cleaned_text.strip()
        
        # Try different price patterns
        price_patterns = [
            r'(\d+\.\d{2})',  # 123.45
            r'(\d+\.\d{1})',  # 123.4
            r'(\d+)',         # 123
        ]
        
        for pattern in price_patterns:
            price_match = re.search(pattern, cleaned_text)
            if price_match:
                try:
                    price = float(price_match.group(1))
                    # Validate price is reasonable
                    if 0.01 <= price <= 50000:
                        return price
                except ValueError:
                    continue
        
        return 0.0

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

