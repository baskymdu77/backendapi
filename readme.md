# Construction Management API

A FastAPI-based API for processing PDF and image files using OpenAI's API.

## Features

- Process PDF files with OpenAI's GPT-4
- Process images with OpenAI's Vision API
- Send prompts directly to OpenAI API
- Detailed logging for debugging and monitoring

## Windows Installation Guide

### Prerequisites

- Windows 10 or 11
- Python 3.9+ installed
- Git (optional, for cloning the repository)

### Step 1: Install Python

1. Download Python from [python.org](https://www.python.org/downloads/windows/)
2. Run the installer
3. **Important**: Check the box that says "Add Python to PATH" during installation
4. Complete the installation

### Step 2: Clone or Download the Repository

**Option 1: Using Git**
```
git clone <repository-url>
cd construction_manage_api
```

**Option 2: Download ZIP**
1. Download the ZIP file of the repository
2. Extract it to a folder of your choice
3. Open Command Prompt and navigate to the extracted folder
```
cd path\to\construction_manage_api
```

### Step 3: Create a Virtual Environment

```
python -m venv venv
```

### Step 4: Activate the Virtual Environment

```
venv\Scripts\activate
```

You should see `(venv)` appear at the beginning of your command prompt line, indicating the virtual environment is active.

### Step 5: Install Dependencies

For Windows, use the Windows-specific requirements file that excludes uvloop (which is not supported on Windows):

```
pip install -r requirements-windows.txt
```

**Note:** If you're using the regular `requirements.txt` file on Windows, you'll encounter an error with uvloop. The Windows-specific file addresses this issue.

### Step 6: Set Up Environment Variables

1. Create a file named `.env` in the root directory of the project
2. Add your OpenAI API key to the file:
```
OPENAI_API_KEY=your_api_key_here
```

## Running the API

### Start the Server

With the virtual environment activated:

```
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

Or use the provided batch script (create a file named `run.bat` with the following content):

```batch
@echo off
echo Loading environment variables from .env file...
for /f "tokens=*" %%a in (.env) do (
    set %%a
)

echo Starting FastAPI server...
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

Then run:
```
run.bat
```

### Access the API

- API documentation: http://localhost:8001/docs
- Alternative API documentation: http://localhost:8001/redoc

## API Endpoints

### Process PDF or Image

```
POST /openai/process-file
```

**Form Parameters:**
- `file`: PDF or image file (supported formats: PDF, JPG, PNG, etc.)
- `prompt`: Instructions for processing the file
- `model`: OpenAI model to use (default: gpt-4)

### Process PDF (Legacy Endpoint)

```
POST /openai/process-pdf
```

**Form Parameters:**
- `file`: PDF file
- `prompt`: Instructions for processing the PDF

### Prompt Only

```
POST /openai/prompt-only
```

**Form Parameters:**
- `prompt`: Prompt to send to OpenAI

## Troubleshooting

### Common Issues

1. **OpenAI API Key Issues**
   - Ensure your API key is correctly set in the `.env` file
   - Check that the `.env` file is in the root directory of the project

2. **Port Already in Use**
   - If port 8001 is already in use, change the port number in the run command

3. **Package Installation Errors**
   - If you encounter issues with Pillow installation, try:
     ```
     pip install --upgrade pip
     pip install pillow --no-cache-dir
     ```

4. **File Upload Issues**
   - Ensure file size is within reasonable limits
   - Check that file format is supported

## License

[License information here]

