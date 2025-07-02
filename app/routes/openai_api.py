import os
import logging
import time
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from typing import Optional
from PyPDF2 import PdfReader
from openai import OpenAI
import io
import httpx

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
    logger.info("Successfully retrieved OpenAI API key")
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

@router.post("/process-pdf")
async def process_pdf(
    file: UploadFile = File(...),
    prompt: str = Form(...)
):
    """
    Process a PDF file with a given prompt using OpenAI API.
    
    - file: PDF file to process
    - prompt: Instructions for processing the PDF content
    """
    start_time = time.time()
    request_id = f"pdf-{int(start_time)}"
    logger.info(f"[{request_id}] Processing PDF request: filename={file.filename}, prompt_length={len(prompt)}")
    
    try:
        # Read the PDF file
        logger.info(f"[{request_id}] Reading PDF file")
        contents = await file.read()
        file_size = len(contents)
        logger.info(f"[{request_id}] File size: {file_size} bytes")
        
        # Check if the file is a PDF
        if not file.filename.lower().endswith('.pdf'):
            logger.warning(f"[{request_id}] Invalid file format: {file.filename}")
            raise HTTPException(status_code=400, detail="File must be a PDF")
        
        # Extract text from PDF
        logger.info(f"[{request_id}] Extracting text from PDF")
        pdf_text = extract_text_from_pdf(contents)
        
        # Initialize OpenAI client
        logger.info(f"[{request_id}] Initializing OpenAI client")
        client = get_openai_client()
        
        # Call OpenAI API with the PDF text and prompt
        logger.info(f"[{request_id}] Calling OpenAI API with PDF content and prompt")
        api_start_time = time.time()
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that processes PDF content."},
                {"role": "user", "content": f"PDF Content: {pdf_text}\n\nPrompt: {prompt}"}
            ]
        )
        api_duration = time.time() - api_start_time
        logger.info(f"[{request_id}] OpenAI API call completed in {api_duration:.2f} seconds")
        
        # Return the response
        total_duration = time.time() - start_time
        logger.info(f"[{request_id}] Request completed successfully in {total_duration:.2f} seconds")
        return {
            "filename": file.filename,
            "prompt": prompt,
            "response": response.choices[0].message.content
        }
    
    except Exception as e:
        logger.error(f"[{request_id}] Error processing PDF: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/prompt-only")
async def prompt_only(prompt: str = Form(...)):
    """
    Process a prompt using OpenAI API without any PDF file.
    
    - prompt: The prompt to process
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
            model="gpt-3.5-turbo",
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
