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
                products = await self._extract_products(soup, request_id, location)
                
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

    async def _extract_products(self, soup: BeautifulSoup, request_id: str, location: str) -> List[Dict[str, Any]]:
        """Extract product information from search results"""
        products = []
        
        # Find product containers
        product_containers = soup.find_all('div', {'data-testid': 'product-pod'}) or \
                           soup.find_all('div', class_=re.compile(r'product-pod|plp-pod'))
        
        if not product_containers:
            # Try alternative selectors
            product_containers = soup.find_all('div', class_=re.compile(r'browse-search__pod'))
        
        logger.info(f"[{request_id}] Found {len(product_containers)} product containers")
        
        for idx, container in enumerate(product_containers[:24]):  # Limit to 24 products
            try:
                product = await self._extract_single_product(container, idx + 1, location)
                if product:
                    products.append(product)
            except Exception as e:
                logger.warning(f"[{request_id}] Error extracting product {idx + 1}: {str(e)}")
                continue
        
        return products

    async def _extract_single_product(self, container: BeautifulSoup, position: int, location: str) -> Optional[Dict[str, Any]]:
        """Extract information for a single product"""
        try:
            # Debug: Log the container HTML structure for the first product
            if position == 1:
                logger.info(f"First product container HTML snippet: {str(container)[:500]}...")
            # Extract product link and ID - updated selectors
            link_selectors = [
                'div[data-testid="product-pod"] a'
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
                
            # Scrape the product detail page to get more information
            product_details = {}# await self._scrape_product_detail_page(href, location)
            # If we got additional details, use them to enhance our data
            enhanced_brand = product_details.get('brand')
            enhanced_model = product_details.get('model_number')
            additional_images = product_details.get('images', [])
            specifications = product_details.get('specifications', {})
            description = product_details.get('description', '')
            details_html = product_details.get('html', '')
            
            # Extract title - updated selectors based on Home Depot structure
            title_selectors = [
                'span[data-testid="attribute-product-label"]',
            ]
            
            title = "Product Title Not Found"
            for selector in title_selectors:
                title_elem = container.select_one(selector)
                if title_elem:
                    title = title_elem.get_text(strip=True)
                    break

            # Extract price - improved logic for variable pricing including range-price
            price_selectors = [
                "#range-price",  # Specific Home Depot price range selector
            ]
            
            price = 0.0
            price_text = ""
            price_range = None
            
            # First try to find specific price elements
            for selector in price_selectors:
                try:
                    price_elem = container.select_one(selector)
                    if price_elem:
                        # Special handling for range-price structure
                        if selector == "#range-price":
                            # Extract prices from the specific Home Depot range structure
                            price_spans = price_elem.select('span.sui-text-4xl')
                            if len(price_spans) >= 2:
                                # Get the two main price numbers
                                price1 = price_spans[0].get_text(strip=True)
                                price2 = price_spans[1].get_text(strip=True)
                                
                                # Get the decimal parts if they exist
                                decimal_spans = price_elem.select('span.sui-text-xs')
                                decimal1 = decimal2 = "00"
                                if len(decimal_spans) >= 4:  # $, price1, decimal1, $, price2, decimal2
                                    decimal1 = decimal_spans[1].get_text(strip=True) if len(decimal_spans) > 1 else "00"
                                    decimal2 = decimal_spans[3].get_text(strip=True) if len(decimal_spans) > 3 else "00"
                                
                                # Construct the price range
                                price1_full = f"${price1}.{decimal1}"
                                price2_full = f"${price2}.{decimal2}"
                                price_range = f"{price1_full} - {price2_full}"
                                price = self._parse_price(price1_full)  # Use the lower price
                                
                                print(f"Range price extracted: {price_range}, using: {price}")
                                break
                        
                        price_text = price_elem.get_text(strip=True)
                        print(f"Price text: {price_text}")
                        
                       
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
          
            # Extract rating and reviews from the specific Home Depot structure
            # First try to find the ratings link which contains both rating and review count
            rating = 0.0
            reviews = 0
            
            # Look for the specific Home Depot ratings element
            ratings_link = container.select_one('a[data-testid="product-pod__ratings-link"]')
            if ratings_link:
                print("Found ratings link")
                # Extract rating from aria-label in span
                rating_span = ratings_link.select_one('span[aria-label*="Stars"]')
                if rating_span:
                    aria_label = rating_span.get('aria-label', '')
                    print(f"Rating aria-label: {aria_label}")
                    rating_match = re.search(r'(\d+(?:\.\d+)?)', aria_label)
                    if rating_match:
                        rating = float(rating_match.group(1))
                        print(f"Extracted rating: {rating}")
                
                # Extract the review count and exact rating from the text
                rating_text_span = ratings_link.select_one('span.sui-font-regular.sui-text-xs span.sui-font-regular.sui-text-xs')
                if rating_text_span:
                    rating_text = rating_text_span.get_text(strip=True)
                    print(f"Rating text: {rating_text}")
                    # Text format is typically like "(4.2 / 501)" where 4.2 is rating and 501 is review count
                    rating_review_match = re.search(r'\(\s*(\d+(?:\.\d+)?)\s*\/\s*(\d+)\s*\)', rating_text)
                    if rating_review_match:
                        rating = float(rating_review_match.group(1))
                        reviews = int(rating_review_match.group(2))
                        print(f"Extracted rating: {rating}, reviews: {reviews}")
                
                # If we still don't have the review count, look for it in subtle text
                if reviews == 0:
                    review_span = ratings_link.select_one('span.sui-text-subtle')
                    if review_span:
                        review_text = review_span.get_text(strip=True)
                        print(f"Review text: {review_text}")
                        review_match = re.search(r'(\d+)', review_text)
                        if review_match:
                            reviews = int(review_match.group(1))
                            print(f"Extracted reviews: {reviews}")
            
        
            # Build product data
            product = {
                "position": position,
                "product_id": product_id,
                "title": title,
                "link": f"https://www.homedepot.com{href}" if href.startswith('/') else href,
                "brand": enhanced_brand or brand,  # Use enhanced brand if available
                "model_number": enhanced_model or model_number,  # Use enhanced model if available
                "price": price,
                "rating": rating,
                "reviews": reviews,
                "html": str(container),
                "details_html": details_html
            }
            
            # Add price range info if available
            if price_range:
                product["price_range"] = price_range
                
            # Add additional details from product page if available
            if additional_images:
                product["images"] = additional_images
                
            if specifications:
                product["specifications"] = specifications
                
            if description:
                product["description"] = description
            
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

    async def _scrape_product_detail_page(self, href: str, zip_code: str) -> Dict[str, Any]:
        """Scrape the product detail page to get more detailed information"""
        try:
            # Build the full URL if it's a relative URL
            if href.startswith('/'):
                product_url = f"https://www.homedepot.com{href}"
            else:
                product_url = href
                
            print(f"Scraping product detail page: {product_url}")
            
            # Set cookies for location
            cookies = {
                'THD_CACHE_NAV_SESSION': '1',
                'THD_SESSION': 'zipCode=' + zip_code,
                'THD_PERSIST': 'C4%3D' + zip_code + '%2BUS',
                'zipCode': zip_code
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(product_url, headers=self.headers, cookies=cookies)
                
                if response.status_code != 200:
                    print(f"Failed to fetch product detail page: {response.status_code}")
                    return {}
                    
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Extract detailed product information
                details = {}
                
                # Get more accurate brand information
                brand_elem = soup.select_one('span[data-testid="attribute-brandname-inline"]')
                if brand_elem:
                    details['brand'] = brand_elem.get_text(strip=True)
                
                # Get more accurate model number
                model_section = soup.select_one('div:contains("Model#")')
                if model_section:
                    model_text = model_section.get_text(strip=True)
                    if "Model#" in model_text:
                        details['model_number'] = model_text.split("Model#")[-1].strip()
                
                # Get detailed specifications
                specs = {}
                spec_rows = soup.select('div[data-testid="specifications"] div.sui-flex.sui-flex-row')
                for row in spec_rows:
                    columns = row.select('div')
                    if len(columns) >= 2:
                        key = columns[0].get_text(strip=True)
                        value = columns[1].get_text(strip=True)
                        if key and value:
                            specs[key] = value
                
                if specs:
                    details['specifications'] = specs
                
                # Get product description
                desc_elem = soup.select_one('div[data-testid="product-description"]')
                if desc_elem:
                    details['description'] = desc_elem.get_text(strip=True)
                
                # Get high-quality images
                images = []
                img_elems = soup.select('img[data-testid="product-image"]')
                for img in img_elems:
                    src = img.get('src')
                    if src and 'homedepot' in src and not src.endswith('.gif'):
                        images.append(src)
                
                if images:
                    details['images'] = images

                details['html'] = str(soup)
                
                print(f"Extracted product details: {details.keys()}")
                return details
                
        except Exception as e:
            print(f"Error scraping product detail page: {str(e)}")
            return {}

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

