import os
import logging
import time
import base64
from enum import Enum
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import JSONResponse
from typing import Optional, List
from PyPDF2 import PdfReader
from openai import OpenAI
import io
import httpx
from PIL import Image

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/openai", tags=["OpenAI"])

# Get OpenAI API key from environment variable
def get_openai_api_key():
    logger.info("Getting OpenAI API key from environment variables")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.error("OpenAI API key not found in environment variables")
        raise HTTPException(status_code=500, detail="OpenAI API key not found in environment variables")
    logger.info("Successfully retrieved OpenAI API key", api_key)
    return api_key

# Initialize OpenAI client
def get_openai_client():
    logger.info("Initializing OpenAI client")
    try:
        api_key = get_openai_api_key()
        # Create a basic client with only the API key
        # This avoids any issues with unexpected arguments like 'proxies'
        import os
        os.environ["OPENAI_API_KEY"] = api_key
   
        http_client = httpx.Client()
        client = OpenAI(api_key=api_key, http_client=http_client)    
        logger.info("OpenAI client initialized successfully")
        return client
    except Exception as e:
        logger.error(f"Error initializing OpenAI client: {str(e)}")
        raise

class FileType(str, Enum):
    PDF = "pdf"
    IMAGE = "image"
    UNKNOWN = "unknown"

# Determine file type based on extension
def get_file_type(filename):
    if filename.lower().endswith(('.pdf')):
        return FileType.PDF
    elif filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.tif')):
        return FileType.IMAGE
    else:
        return FileType.UNKNOWN

# Extract text from PDF
def extract_text_from_pdf(pdf_file):
    logger.info("Starting PDF text extraction")
    try:
        # Create BytesIO object from the PDF file bytes
        pdf_bytes = io.BytesIO(pdf_file)
        # Create PDF reader
        pdf_reader = PdfReader(pdf_bytes)
        # Extract text from all pages
        text = ""
        page_count = len(pdf_reader.pages)
        logger.info(f"PDF has {page_count} pages")
        
        for i, page in enumerate(pdf_reader.pages):
            logger.debug(f"Extracting text from page {i+1}/{page_count}")
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        
        logger.info(f"Successfully extracted {len(text)} characters from PDF")
        return text
    except Exception as e:
        logger.error(f"Error extracting text from PDF: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Error extracting text from PDF: {str(e)}")

# Process image file for OpenAI vision API
def process_image_for_vision(image_file):
    logger.info("Processing image for vision API")
    try:
        # Open image using PIL
        img = Image.open(io.BytesIO(image_file))
        
        # Convert to RGB if needed (handles RGBA, etc.)
        if img.mode != "RGB":
            img = img.convert("RGB")
        
        # Save to bytes
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='JPEG')
        img_byte_arr = img_byte_arr.getvalue()
        
        # Encode as base64
        base64_image = base64.b64encode(img_byte_arr).decode('utf-8')
        logger.info(f"Successfully processed image, size: {len(base64_image)} bytes")
        
        return base64_image
    except Exception as e:
        logger.error(f"Error processing image: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Error processing image: {str(e)}")

@router.post("/process-file")
async def process_file(
    file: UploadFile = File(...),
    prompt: str = Form(...),
    model: str = Form("o4-mini"),
    temperature: float = Form(0.7),
):
    """
    Process a PDF or image file with a given prompt using OpenAI API.

    - file: PDF or image file to process
    - prompt: Instructions for processing the file content
    - model: OpenAI model to use (default: o4-mini)
    - temperature: Controls randomness (0.0-2.0, default: 0.7)
    """
    start_time = time.time()
    request_id = f"file-{int(start_time)}"
    logger.info(f"[{request_id}] Processing file request: filename={file.filename}, prompt_length={len(prompt)}")
    
    try:
        # Read the file
        logger.info(f"[{request_id}] Reading file")
        contents = await file.read()
        file_size = len(contents)
        logger.info(f"[{request_id}] File size: {file_size} bytes")
        
        # Determine file type
        file_type = get_file_type(file.filename)
        logger.info(f"[{request_id}] Detected file type: {file_type}")
        
        if file_type == FileType.UNKNOWN:
            logger.warning(f"[{request_id}] Unsupported file format: {file.filename}")
            raise HTTPException(status_code=400, detail="Unsupported file format. Please upload a PDF or image file.")
        
        # Initialize OpenAI client
        logger.info(f"[{request_id}] Initializing OpenAI client")
        client = get_openai_client()
        api_start_time = time.time()
        
        # Process based on file type
        if file_type == FileType.PDF:
            # Extract text from PDF
            logger.info(f"[{request_id}] Extracting text from PDF")
            pdf_text = extract_text_from_pdf(contents)
            
            # Call OpenAI API with the PDF text and prompt
            logger.info(f"[{request_id}] Calling OpenAI API with PDF content and prompt")
            response = client.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that processes PDF content."},
                    {"role": "user", "content": f"PDF Content: {pdf_text}\n\nPrompt: {prompt}"}
                ]
            )
        
        elif file_type == FileType.IMAGE:
            # Process image for vision API
            logger.info(f"[{request_id}] Processing image for vision API")
            base64_image = process_image_for_vision(contents)
            
            # Call OpenAI Vision API with the image and prompt
            logger.info(f"[{request_id}] Calling OpenAI Vision API with image and prompt")
            response = client.chat.completions.create(
                model=model,  # Use vision model for images
                temperature=temperature,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that analyzes images."},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=1000
            )
        
        api_duration = time.time() - api_start_time
        logger.info(f"[{request_id}] OpenAI API call completed in {api_duration:.2f} seconds")
        
        # Return the response
        total_duration = time.time() - start_time
        logger.info(f"[{request_id}] Request completed successfully in {total_duration:.2f} seconds")
        return {
            "filename": file.filename,
            "file_type": file_type,
            "prompt": prompt,
            "response": response.choices[0].message.content
        }
    
    except Exception as e:
        logger.error(f"[{request_id}] Error processing file: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/prompt-only")
async def prompt_only(
    prompt: str = Form(...),
    model: str = Form("gpt-3.5-turbo"),
    temperature: float = Form(0.7)
):
    """
    Process a prompt using OpenAI API without any PDF file.

    - prompt: The prompt to process
    - model: OpenAI model to use (default: gpt-3.5-turbo)
    - temperature: Controls randomness (0.0-2.0, default: 0.7)
    """
    start_time = time.time()
    request_id = f"prompt-{int(start_time)}"
    logger.info(f"[{request_id}] Processing prompt-only request: prompt_length={len(prompt)}")
    
    try:
        # Initialize OpenAI client
        logger.info(f"[{request_id}] Initializing OpenAI client")
        client = get_openai_client()
        
        # Call OpenAI API with just the prompt
        logger.info(f"[{request_id}] Calling OpenAI API with prompt")
        api_start_time = time.time()
        response = client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt}
            ]
        )
        api_duration = time.time() - api_start_time
        logger.info(f"[{request_id}] OpenAI API call completed in {api_duration:.2f} seconds")
        
        # Return the response
        total_duration = time.time() - start_time
        logger.info(f"[{request_id}] Request completed successfully in {total_duration:.2f} seconds")
        return {
            "prompt": prompt,
            "response": response.choices[0].message.content
        }
    
    except Exception as e:
        logger.error(f"[{request_id}] Error processing prompt: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
